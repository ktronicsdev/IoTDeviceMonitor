echo off
REM Check if the script is running on Windows or Unix-based system
IF NOT "%OS%"=="" (
    REM Windows Environment

    REM Set environment variable
    set AZURE_SQL_CONNECTIONSTRING=your_connection_string

    REM Run AzureFunction project
    call mvn clean install
    call mvn azurefunctions:run
)

# Unix-based environment (Linux, macOS, etc.)

#!/bin/bash

# Set environment variable
export AZURE_SQL_CONNECTIONSTRING="your_connection_string"

# Run AzureFunction project
mvn clean install
mvn azurefunctions:run
