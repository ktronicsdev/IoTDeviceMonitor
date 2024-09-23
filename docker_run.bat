@echo off
setlocal

:: Set the environment variables
set AzureWebJobsStorage= <AzureWebJobsStorage>
set JDBC_DATABASE_URL= <JDBC_DATABASE_URL>

@REM Set the timer schedule to run the function every day at 12:00 AM. Set the CRON expression to to your desired schedule. (s mm hh d m w)
set TIMER_SCHEDULE = 0 00 00 * * *

set numberOfDaysToCheckAvailability = 3

:: Build the Docker image
docker build -t iot-device-abnormal-detector .

:: Run the Docker container with the environment variables
docker run -e AzureWebJobsStorage=%AzureWebJobsStorage% -e JDBC_DATABASE_URL=%JDBC_DATABASE_URL% iot-device-abnormal-detector

endlocal
