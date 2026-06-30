"""Elevation data retrieval module"""

import requests
import logging
import json
from config import OPEN_ELEVATION_URL, OPEN_METEO_ELEVATION_URL


class ElevationService:
    """Fetch elevation data from Open-Elevation API in batch"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()  # Reuse TCP connection across API calls

    def _fetch_chunk(self, coords_chunk, retry_count=0, max_retries=3, status_callback=None):
        """Fetch one chunk of coordinates from Open-Elevation API with exponential backoff."""
        import time
        locations = [{'latitude': lat, 'longitude': lon} for lat, lon in coords_chunk]
        payload = {'locations': locations}
        self.logger.info(f"Open-Elevation request: chunk_size={len(coords_chunk)}")

        try:
            response = self.session.post(
                OPEN_ELEVATION_URL,
                json=payload,
                timeout=30,
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
            )
            response.raise_for_status()

            data = response.json()
            if status_callback:
                raw = json.dumps(data, ensure_ascii=True, separators=(',', ':'))
                status_callback(f"Elevation API raw response: {raw[:3000]}")
            results = data.get('results', [])
            if results:
                sample = results[0]
                self.logger.info(
                    "Open-Elevation response: "
                    f"results={len(results)} sample=(lat={sample.get('latitude')}, "
                    f"lon={sample.get('longitude')}, elev={sample.get('elevation')})"
                )
            else:
                self.logger.warning("Open-Elevation response: results=0")

            # Use the *original* coordinates as keys (not the API echo) because
            # the API may return lower-precision lat/lon that won't match the
            # float keys built from the ride DataFrame, causing all lookups to
            # miss and fall back to FIT altitude.
            elevation_map = {}
            for i, result in enumerate(results):
                elev = result.get('elevation')
                if i < len(coords_chunk) and elev is not None:
                    elevation_map[coords_chunk[i]] = elev
            return elevation_map
        except requests.HTTPError as e:
            if e.response.status_code == 429 and retry_count < max_retries:
                # Rate limited: exponential backoff
                wait_time = (2 ** retry_count) + (retry_count * 0.1)  # 1s, 2s, 4s
                self.logger.info(f"Rate limited (429). Retrying in {wait_time:.1f}s (attempt {retry_count + 1}/{max_retries})")
                time.sleep(wait_time)
                return self._fetch_chunk(coords_chunk, retry_count + 1, max_retries, status_callback=status_callback)
            raise
    
    def get_elevations_batch(self, coordinates, chunk_size=500, status_callback=None):
        """
        Fetch elevations from Open-Elevation API with automatic chunking.

        Args:
            coordinates (list): List of tuples (latitude, longitude)
            chunk_size (int): Coordinates per API request to avoid 413 payload errors

        Returns:
            dict: Maps (lat, lon) tuples to elevation values (meters), or None on total failure
        """
        if not coordinates:
            return {}

        # Deduplicate while preserving order to minimize API calls
        unique_coords = list(dict.fromkeys(coordinates))

        merged_map = {}
        failed_chunks = 0

        for i in range(0, len(unique_coords), chunk_size):
            chunk = unique_coords[i:i + chunk_size]
            try:
                chunk_map = self._fetch_chunk(chunk, status_callback=status_callback)
                merged_map.update(chunk_map)
            except requests.HTTPError as e:
                status = getattr(e.response, 'status_code', None)
                # Retry once with smaller chunks for payload-too-large errors
                if status == 413 and len(chunk) > 50:
                    self.logger.info(f"Chunk too large ({len(chunk)}), retrying with smaller chunks")
                    sub_size = max(50, len(chunk) // 2)
                    for j in range(0, len(chunk), sub_size):
                        sub_chunk = chunk[j:j + sub_size]
                        try:
                            sub_map = self._fetch_chunk(sub_chunk, status_callback=status_callback)
                            merged_map.update(sub_map)
                        except Exception as sub_e:
                            failed_chunks += 1
                            self.logger.warning(f"Failed sub-chunk {j//sub_size + 1} in chunk {i//chunk_size + 1}: {sub_e}")
                elif status == 429:
                    # Rate limited: already handled by _fetch_chunk with exponential backoff
                    failed_chunks += 1
                    self.logger.warning(f"Failed chunk {i//chunk_size + 1} after retries: {e}")
                else:
                    failed_chunks += 1
                    self.logger.warning(f"Failed chunk {i//chunk_size + 1}: {e}")
            except Exception as e:
                failed_chunks += 1
                self.logger.warning(f"Failed chunk {i//chunk_size + 1}: {e}")

        if not merged_map and failed_chunks > 0:
            self.logger.warning("Failed to fetch elevations from Open-Elevation API for all chunks")
            return None

        self.logger.info(
            f"Successfully fetched {len(merged_map)} elevations from Open-Elevation API "
            f"({len(unique_coords)} unique coords, {failed_chunks} failed chunks)"
        )
        return merged_map

    def apply_to_dataframe(self, df, status_callback=None):
        """Apply Open-Elevation API elevations onto a ride DataFrame.

        Preserves the original FIT altitude in ``altitude_fit`` and writes the
        API-fetched values to both ``altitude_api`` and ``altitude``.

        Args:
            df (DataFrame): Ride data with at least ``latitude``/``longitude`` columns.
            status_callback (callable | None): Optional callable for status messages.

        Returns:
            tuple[DataFrame, str]: Updated DataFrame and a human-readable elevation
            source description.
        """
        import numpy as np

        if 'altitude' in df.columns and 'altitude_fit' not in df.columns:
            df['altitude_fit'] = df['altitude'].copy()

        if 'latitude' not in df.columns or 'longitude' not in df.columns:
            source = 'FIT file (no GPS coordinates)'
            self.logger.info("No GPS columns available for Open-Elevation API, using FIT altitude")
            if status_callback:
                status_callback("Elevation API: no GPS columns, using FIT altitude")
            return df, source

        valid_coords = df[['latitude', 'longitude']].dropna()
        if len(valid_coords) == 0:
            source = 'FIT file (no GPS coordinates)'
            self.logger.info("No valid GPS coordinates for Open-Elevation API, using FIT altitude")
            if status_callback:
                status_callback("Elevation API: 0 valid coordinates, using FIT altitude")
            return df, source

        # Sample route every ~100 m to reduce API load, then interpolate back to all points.
        sampled_df = _sample_dataframe_every_distance(df, interval_m=100.0)
        sampled_coords_df = sampled_df[['latitude', 'longitude']].dropna()
        coordinates = list(dict.fromkeys(zip(sampled_coords_df['latitude'], sampled_coords_df['longitude'])))
        if status_callback:
            status_callback(
                f"Elevation API request: sampled_points={len(sampled_df)} "
                f"unique_coords={len(coordinates)} total_points={len(df)}"
            )

        elevation_map = self.get_elevations_batch(coordinates, status_callback=status_callback)

        if elevation_map:
            sampled_elev = []
            for _, row in sampled_df.iterrows():
                key = (row['latitude'], row['longitude'])
                fallback = row.get('altitude_fit', row.get('altitude', np.nan))
                sampled_elev.append(elevation_map.get(key, fallback))

            sampled_df = sampled_df.copy()
            sampled_df['altitude_sampled'] = sampled_elev

            if 'distance' in df.columns and 'distance' in sampled_df.columns and len(sampled_df) >= 2:
                x = sampled_df['distance'].to_numpy(dtype=float)
                y = sampled_df['altitude_sampled'].to_numpy(dtype=float)
                x_full = df['distance'].to_numpy(dtype=float)

                # Keep interpolation numerically stable when duplicate x values exist.
                keep = np.concatenate(([True], np.diff(x) > 0)) if len(x) > 1 else np.array([True])
                x = x[keep]
                y = y[keep]

                if len(x) >= 2:
                    df['altitude_api'] = np.interp(x_full, x, y)
                else:
                    df['altitude_api'] = float(y[0]) if len(y) else np.nan
            else:
                # Fallback: exact coordinate lookup when distance is unavailable.
                def _get_elev(row):
                    key = (row['latitude'], row['longitude'])
                    fallback = row.get('altitude_fit', row.get('altitude', np.nan))
                    return elevation_map.get(key, fallback)

                df['altitude_api'] = df.apply(_get_elev, axis=1)

            df['altitude'] = df['altitude_api']
            source = 'Open-Elevation API'

            first_key = next(iter(elevation_map))
            if status_callback:
                status_callback(
                    f"Elevation API response: mapped={len(elevation_map)} "
                    f"sample=({first_key[0]:.6f},{first_key[1]:.6f},{elevation_map[first_key]})"
                )
            self.logger.info(f"Applied elevations from Open-Elevation API for {len(elevation_map)} unique points")
        else:
            source = 'FIT file (Open-Elevation API failed)'
            self.logger.warning("Open-Elevation API returned no data, keeping FIT altitude")
            if status_callback:
                status_callback("Elevation API: no elevations returned, using FIT altitude")

        return df, source


class OpenMeteoElevationService:
    """Fetch elevation data from Open-Meteo Elevation API in batch"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()  # Reuse TCP connection across API calls

    def _fetch_chunk(self, coords_chunk, retry_count=0, max_retries=3, status_callback=None):
        """Fetch one chunk of coordinates from Open-Meteo Elevation API with exponential backoff."""
        # Open-Meteo expects comma-separated lat/lon strings
        lats = ','.join(str(lat) for lat, lon in coords_chunk)
        lons = ','.join(str(lon) for lat, lon in coords_chunk)
        
        self.logger.info(f"Open-Meteo request: chunk_size={len(coords_chunk)}")

        try:
            response = self.session.get(
                OPEN_METEO_ELEVATION_URL,
                params={'latitude': lats, 'longitude': lons},
                timeout=30,
                headers={'Accept': 'application/json'}
            )
            response.raise_for_status()

            data = response.json()
            if status_callback:
                raw = json.dumps(data, ensure_ascii=True, separators=(',', ':'))
                status_callback(f"Open-Meteo elevation API response: {raw[:3000]}")
            
            elevations = data.get('elevation', [])
            if elevations:
                self.logger.info(
                    f"Open-Meteo response: {len(elevations)} elevations, "
                    f"sample_elev={elevations[0]}m"
                )
            else:
                self.logger.warning("Open-Meteo response: 0 elevations returned")

            # Map coordinates to elevations by index
            elevation_map = {}
            for i, elev in enumerate(elevations):
                if i < len(coords_chunk) and elev is not None:
                    elevation_map[coords_chunk[i]] = elev
            return elevation_map
        except requests.HTTPError as e:
            if e.response.status_code == 429 and retry_count < max_retries:
                # Deterministic retry without jitter/delays.
                self.logger.info(
                    f"Rate limited (429). Retrying immediately "
                    f"(attempt {retry_count + 1}/{max_retries})"
                )
                return self._fetch_chunk(coords_chunk, retry_count + 1, max_retries, status_callback=status_callback)
            elif e.response.status_code == 400:
                # Invalid input (e.g., coordinates out of range)
                self.logger.warning(f"Invalid coordinates in chunk: {e.response.text}")
                return {}
            raise
    
    def get_elevations_batch(self, coordinates, chunk_size=100, status_callback=None):
        """
        Fetch elevations from Open-Meteo Elevation API with automatic chunking.
        Open-Meteo allows up to 100 coordinates per request.

        Args:
            coordinates (list): List of tuples (latitude, longitude)
            chunk_size (int): Coordinates per API request (max 100 for Open-Meteo)

        Returns:
            dict: Maps (lat, lon) tuples to elevation values (meters), or None on total failure
        """
        if not coordinates:
            return {}

        # Open-Meteo hard limit: exactly 100 coordinate pairs per request (last chunk may be smaller).
        chunk_size = 100

        # Deduplicate while preserving order to minimize API calls
        unique_coords = list(dict.fromkeys(coordinates))

        merged_map = {}
        failed_chunks = 0

        for i in range(0, len(unique_coords), chunk_size):
            chunk = unique_coords[i:i + chunk_size]
            try:
                chunk_map = self._fetch_chunk(chunk, status_callback=status_callback)
                merged_map.update(chunk_map)
            except requests.HTTPError as e:
                status = getattr(e.response, 'status_code', None)
                if status == 400:
                    # Filter out invalid coordinates and retry
                    self.logger.info(f"Chunk {i//chunk_size + 1} had invalid coordinates, attempting smaller subset")
                    for j, (lat, lon) in enumerate(chunk):
                        if -90 <= lat <= 90 and -180 <= lon <= 180:
                            try:
                                sub_map = self._fetch_chunk([(lat, lon)], status_callback=status_callback)
                                merged_map.update(sub_map)
                            except Exception as sub_e:
                                self.logger.warning(f"Failed single coordinate at index {j}: {sub_e}")
                        else:
                            self.logger.warning(f"Skipped invalid coordinate: lat={lat}, lon={lon}")
                    failed_chunks += 1
                elif status == 429:
                    # Rate limited: already handled by _fetch_chunk with exponential backoff
                    failed_chunks += 1
                    self.logger.warning(f"Failed chunk {i//chunk_size + 1} after retries: {e}")
                else:
                    failed_chunks += 1
                    self.logger.warning(f"Failed chunk {i//chunk_size + 1}: {e}")
            except Exception as e:
                failed_chunks += 1
                self.logger.warning(f"Failed chunk {i//chunk_size + 1}: {e}")

        if not merged_map and failed_chunks > 0:
            self.logger.warning("Failed to fetch elevations from Open-Meteo Elevation API for all chunks")
            return None

        self.logger.info(
            f"Successfully fetched {len(merged_map)} elevations from Open-Meteo Elevation API "
            f"({len(unique_coords)} unique coords, {failed_chunks} failed chunks)"
        )
        return merged_map

    def apply_to_dataframe(self, df, status_callback=None):
        """Apply Open-Meteo Elevation API elevations onto a ride DataFrame.

        Preserves the original FIT altitude in ``altitude_fit`` and writes the
        API-fetched values to both ``altitude_api`` and ``altitude``.

        Args:
            df (DataFrame): Ride data with at least ``latitude``/``longitude`` columns.
            status_callback (callable | None): Optional callable for status messages.

        Returns:
            tuple[DataFrame, str]: Updated DataFrame and a human-readable elevation
            source description.
        """
        import numpy as np

        if 'altitude' in df.columns and 'altitude_fit' not in df.columns:
            df['altitude_fit'] = df['altitude'].copy()

        if 'latitude' not in df.columns or 'longitude' not in df.columns:
            source = 'FIT file (no GPS coordinates)'
            self.logger.info("No GPS columns available for Open-Meteo Elevation API, using FIT altitude")
            if status_callback:
                status_callback("Elevation API: no GPS columns, using FIT altitude")
            return df, source

        valid_coords = df[['latitude', 'longitude']].dropna()
        if len(valid_coords) == 0:
            source = 'FIT file (no GPS coordinates)'
            self.logger.info("No valid GPS coordinates for Open-Meteo Elevation API, using FIT altitude")
            if status_callback:
                status_callback("Elevation API: 0 valid coordinates, using FIT altitude")
            return df, source

        # Sample route every ~100 m to reduce API load, then interpolate back to all points.
        sampled_df = _sample_dataframe_every_distance(df, interval_m=100.0)
        sampled_coords_df = sampled_df[['latitude', 'longitude']].dropna()
        coordinates = list(dict.fromkeys(zip(sampled_coords_df['latitude'], sampled_coords_df['longitude'])))
        if status_callback:
            status_callback(
                f"Open-Meteo request: sampled_points={len(sampled_df)} "
                f"unique_coords={len(coordinates)} total_points={len(df)}"
            )

        elevation_map = self.get_elevations_batch(coordinates, status_callback=status_callback)

        if elevation_map:
            sampled_elev = []
            for _, row in sampled_df.iterrows():
                key = (row['latitude'], row['longitude'])
                fallback = row.get('altitude_fit', row.get('altitude', np.nan))
                sampled_elev.append(elevation_map.get(key, fallback))

            sampled_df = sampled_df.copy()
            sampled_df['altitude_sampled'] = sampled_elev

            if 'distance' in df.columns and 'distance' in sampled_df.columns and len(sampled_df) >= 2:
                x = sampled_df['distance'].to_numpy(dtype=float)
                y = sampled_df['altitude_sampled'].to_numpy(dtype=float)
                x_full = df['distance'].to_numpy(dtype=float)

                keep = np.concatenate(([True], np.diff(x) > 0)) if len(x) > 1 else np.array([True])
                x = x[keep]
                y = y[keep]

                if len(x) >= 2:
                    df['altitude_api'] = np.interp(x_full, x, y)
                else:
                    df['altitude_api'] = float(y[0]) if len(y) else np.nan
            else:
                def _get_elev(row):
                    key = (row['latitude'], row['longitude'])
                    fallback = row.get('altitude_fit', row.get('altitude', np.nan))
                    return elevation_map.get(key, fallback)

                df['altitude_api'] = df.apply(_get_elev, axis=1)

            df['altitude'] = df['altitude_api']
            source = 'Open-Meteo Elevation API'

            first_key = next(iter(elevation_map))
            if status_callback:
                status_callback(
                    f"Open-Meteo Elevation API response: mapped={len(elevation_map)} "
                    f"sample=({first_key[0]:.6f},{first_key[1]:.6f},{elevation_map[first_key]})"
                )
            self.logger.info(f"Applied elevations from Open-Meteo Elevation API for {len(elevation_map)} unique points")
        else:
            source = 'FIT file (Open-Meteo Elevation API failed)'
            self.logger.warning("Open-Meteo Elevation API returned no data, keeping FIT altitude")
            if status_callback:
                status_callback("Open-Meteo Elevation API: no elevations returned, using FIT altitude")

        return df, source


def get_sample_points_every_100m(df, interval_m=100):
    """
    Get indices of rows where distance increases by ~interval_m.
    
    This reduces the number of API calls by sampling the route at regular
    distance intervals instead of requesting elevation for every GPS point.
    
    Args:
        df (DataFrame): Ride data with 'distance' column
        interval_m (int): Distance interval in meters (default 100m)
    
    Returns:
        list: Indices of sampled points
    """
    if 'distance' not in df.columns or len(df) == 0:
        return []
    
    import numpy as np
    distances = df['distance'].values
    sampled_indices = [0]  # Always include the start
    
    next_target = interval_m
    for i in range(1, len(df)):
        if distances[i] >= next_target:
            sampled_indices.append(i)
            next_target += interval_m
    
    # Always include the end
    if sampled_indices[-1] != len(df) - 1:
        sampled_indices.append(len(df) - 1)
    
    return sampled_indices


def _sample_dataframe_every_distance(df, interval_m=100.0):
    """Return a DataFrame sampled approximately every interval_m by distance."""
    if 'distance' not in df.columns or len(df) == 0:
        return df.copy()

    sampled_indices = get_sample_points_every_100m(df, interval_m=int(interval_m))
    sampled = df.iloc[sampled_indices].copy()
    if len(sampled) == 0:
        return df.iloc[[0]].copy() if len(df) else df.copy()
    return sampled


def get_sample_coordinates_every_100m(df, interval_m=100):
    """
    Extract coordinates from rows sampled at ~interval_m distance intervals.
    
    Args:
        df (DataFrame): Ride data with 'distance', 'latitude', 'longitude' columns
        interval_m (int): Distance interval in meters (default 100m)
    
    Returns:
        list: List of (latitude, longitude) tuples from sampled points
    """
    if 'latitude' not in df.columns or 'longitude' not in df.columns:
        return []
    
    sampled_indices = get_sample_points_every_100m(df, interval_m)
    sampled_rows = df.iloc[sampled_indices].dropna(subset=['latitude', 'longitude'])
    return list(zip(sampled_rows['latitude'], sampled_rows['longitude']))


def apply_elevation_api(df, api_source='open_elevation', status_callback=None):
    """
    Apply elevation API to DataFrame based on selected source.
    
    Samples every ~100m for efficiency before making API calls.
    
    Args:
        df (DataFrame): Ride data with GPS coordinates
        api_source (str): 'open_elevation', 'open_meteo', or 'fit_only'
        status_callback (callable | None): Optional callback for status messages
    
    Returns:
        tuple[DataFrame, str]: Updated DataFrame and elevation source description
    """
    if api_source == 'fit_only':
        if 'altitude' in df.columns:
            source = 'FIT file'
        else:
            source = 'FIT file (no altitude data)'
        if status_callback:
            status_callback(f"Elevation source: {source}")
        return df, source
    
    # Get sampled coordinates every ~100m
    if status_callback:
        status_callback(f"Sampling elevation points every 100m for {api_source}...")
    sampled_coords = get_sample_coordinates_every_100m(df, interval_m=100)
    
    if status_callback:
        status_callback(f"Sampled {len(sampled_coords)} coordinates from full route for elevation API")
    
    if api_source == 'open_meteo':
        service = OpenMeteoElevationService()
    else:  # 'open_elevation' or default
        service = ElevationService()
    
    return service.apply_to_dataframe(df, status_callback=status_callback)
