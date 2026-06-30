"""Sub-segment splitting for per-chunk bearing, slope and acceleration accuracy.

Motivation
----------
Longer steady segments may contain direction changes, gradient variations and
acceleration phases that bias the CdA estimate when treated as a single unit.
Splitting into short sub-segments lets each chunk use:

    - Its own GPS bearing  → local relative wind angle
    - Its own slope mean   → local gradient correction
    - Its own acceleration → local inertial correction

Weather data (temperature, wind speed, wind direction from the API) is
inherited from the parent segment and is NOT re-fetched per sub-segment.
"""

import logging
import pandas as pd

_logger = logging.getLogger(__name__)


def split_into_subsegments(
    segment_df: pd.DataFrame,
    min_duration_s: float = 20.0,
    min_points: int = 10,
) -> list:
    """Split a steady-segment DataFrame into sub-segments.

    Each sub-segment has at least ``min_duration_s`` seconds **and**
    ``min_points`` rows.  Remainders that are too small to stand alone are
    merged into the immediately preceding sub-segment.

    Parameters
    ----------
    segment_df:
        Full steady segment with at minimum ``timestamp`` column.
        Original (non-reset) index is preserved on every returned slice.
    min_duration_s:
        Minimum sub-segment duration in seconds.  Default 20 s.
    min_points:
        Minimum number of rows per sub-segment.  Default 10.

    Returns
    -------
    list[pd.DataFrame]
        Non-overlapping sub-segment DataFrames that together span the full
        parent segment.  Guaranteed: len(result) >= 1.
    """
    n = len(segment_df)

    # --- Guard: too few points to split ----------------------------------------
    if n < 2 * min_points:
        return [segment_df]

    if 'timestamp' not in segment_df.columns:
        return [segment_df]

    total_dur = (
        segment_df['timestamp'].iloc[-1] - segment_df['timestamp'].iloc[0]
    ).total_seconds()

    # --- Guard: too short to split ---------------------------------------------
    if total_dur < 2.0 * min_duration_s:
        return [segment_df]

    subsegments: list = []
    pos = 0  # positional cursor into segment_df

    while pos < n:
        # Advance end_pos until both minimum-points and minimum-duration are met
        end_pos = pos + min_points
        if end_pos > n:
            end_pos = n

        if end_pos < n:
            # Extend until min_duration_s is satisfied
            while end_pos < n:
                dur = (
                    segment_df['timestamp'].iloc[end_pos - 1]
                    - segment_df['timestamp'].iloc[pos]
                ).total_seconds()
                if dur >= min_duration_s:
                    break
                end_pos += 1

        # If the remaining tail would be too thin, absorb it into the current chunk
        remaining = n - end_pos
        if 0 < remaining < min_points:
            end_pos = n

        chunk = segment_df.iloc[pos:end_pos]

        # Tiny remainder at the end: merge into the previous sub-segment
        if len(chunk) < min_points and subsegments:
            prev = subsegments.pop()
            subsegments.append(pd.concat([prev, chunk]))
        else:
            subsegments.append(chunk)

        pos = end_pos

    _logger.debug(
        "split_into_subsegments: %d pts / %.0f s → %d sub-segments (min_dur=%.0f s, min_pts=%d)",
        n, total_dur, len(subsegments), min_duration_s, min_points,
    )
    return subsegments
