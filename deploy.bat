@echo off
echo Deploying ChronoChunk to production server

REM Get server details
set /p SERVER_USER=Enter server username: 
set /p SERVER_IP=Enter server IP: 

REM Create deployment package
echo Creating deployment package...
if exist deploy (rmdir /s /q deploy)
mkdir deploy
xcopy /E /I /Y . deploy
cd deploy
del /q .env
del /q *.bat
rmdir /s /q user_data
rmdir /s /q logs
rmdir /s /q __pycache__
rmdir /s /q .git
cd ..

REM Transfer files
echo Transferring files to server...
scp -r deploy/* %SERVER_USER%@%SERVER_IP%:/opt/chronochunk/

REM Clean up
echo Cleaning up...
rmdir /s /q deploy

echo Deployment package transferred successfully!
echo.
echo IMPORTANT: Don't forget to:
echo 1. Transfer your .env file separately (it contains secrets)
echo 2. Set up the systemd service using README.production.md
echo 3. Start the service with: sudo systemctl start chronochunk
echo Done!