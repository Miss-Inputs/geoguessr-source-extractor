from pathlib import PurePosixPath
from typing import Any

JSSource = str
JSONSource = str
JSONData = Any
FunctionID = int
"""IDs in webpack chunks for each function"""
ModuleID = int
"""Number in filename of webpack chunk (? presumably this is what that does)"""
URL = str
"""URL where I don't really feel like using pydantic_core.Url"""
URLPath = PurePosixPath
"""Path part of a URL where I also couldn't be bothered converting it to a full one"""