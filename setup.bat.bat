@echo off

echo =====================================
echo Setting up Notebook Automation...
echo =====================================

echo.

echo Configuring Git hooks...
git config core.hooksPath hooks

echo.

echo Installing Python dependencies...
python -m pip install -r requirements.txt

echo.

echo =====================================
echo Setup completed successfully.
echo =====================================

pause