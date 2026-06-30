"""Utility functions for CDA analyzer"""

import numpy as np
import pandas as pd
from datetime import datetime

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two GPS coordinates using haversine formula
    
    Args:
        lat1, lon1, lat2, lon2: Latitude and longitude in degrees
    
    Returns:
        float: Distance in meters
    """
    from math import radians, cos, sin, asin, sqrt
    
    # Convert degrees to radians
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    # Radius of earth in meters
    r = 6371000
    
    return c * r

def interpolate_missing_data(df, columns):
    """
    Interpolate missing data in specified columns
    
    Args:
        df (pandas.DataFrame): DataFrame with potential missing data
        columns (list): List of column names to interpolate
    
    Returns:
        pandas.DataFrame: DataFrame with interpolated data
    """
    df = df.copy()
    for col in columns:
        if col in df.columns:
            df[col] = df[col].interpolate(method='linear')
    return df

def calculate_slope(distance, altitude):
    """
    Calculate slope from distance and altitude arrays
    
    Args:
        distance (array): Distance array in meters
        altitude (array): Altitude array in meters
    
    Returns:
        array: Slope in degrees
    """
    distance_diff = np.diff(distance)
    altitude_diff = np.diff(altitude)
    
    # Avoid division by zero
    slope_rad = np.where(distance_diff > 0, 
                        np.arctan2(altitude_diff, distance_diff), 0)
    
    return np.degrees(slope_rad)

def format_duration(seconds):
    """
    Format duration in seconds to human readable string
    
    Args:
        seconds (float): Duration in seconds
    
    Returns:
        str: Formatted duration string
    """
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}min"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"

def validate_parameters(parameters):
    """
    Validate analysis parameters
    
    Args:
        parameters (dict): Parameter dictionary
    
    Returns:
        tuple: (is_valid, error_message)
    """
    required_params = ['rider_mass', 'bike_mass', 'rolling_resistance']
    
    for param in required_params:
        if param not in parameters:
            return False, f"Missing required parameter: {param}"
    
    if parameters['rider_mass'] <= 0:
        return False, "Rider mass must be positive"
    
    if parameters['bike_mass'] <= 0:
        return False, "Bike mass must be positive"
    
    if parameters['rolling_resistance'] < 0:
        return False, "Rolling resistance cannot be negative"
    
    return True, None