@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Schulz S.A. - Iniciar aplicacao (Grupo 45)
cd /d "%~dp0"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PORTA_INICIAL=8080"
set "ARGS_REDE="
if /i "%~1"=="rede" set "ARGS_REDE=--rede"

echo ================================================================
echo  Schulz S.A. - Certificados de Calibracao
echo  Projeto Aplicado III - UniSENAI - Grupo 45
echo ================================================================
echo.

echo [1/4] Verificando Python 3.10 ou superior no PATH...
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python3"
if not defined PY goto sem_python
for /f "delims=" %%V in ('%PY% -c "import platform; print(platform.python_version())"') do set "PYVER=%%V"
echo       OK - Python %PYVER% encontrado (%PY%).
echo.

echo [2/4] Preparando o ambiente virtual (.venv)...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo       Ambiente virtual antigo ou corrompido. Recriando...
        rmdir /s /q ".venv"
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo       Criando .venv ^(primeira execucao^)...
    %PY% -m venv .venv
)
if not exist ".venv\Scripts\activate.bat" goto erro_venv
call ".venv\Scripts\activate.bat"
echo       OK - ambiente virtual ativado.
echo.

echo [3/4] Instalando as bibliotecas do requirements.txt...
set "INSTALAR=1"
fc /b "requirements.txt" ".venv\requirements.instalado" >nul 2>&1 && python -c "import boto3, moto, openpyxl, reportlab, PIL, cryptography" >nul 2>&1 && set "INSTALAR=0"
if "%INSTALAR%"=="0" (
    echo       OK - bibliotecas ja instaladas.
    goto iniciar
)
echo       Isso leva de 1 a 3 minutos na primeira vez...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 goto erro_pip
copy /y "requirements.txt" ".venv\requirements.instalado" >nul
echo       OK - bibliotecas instaladas.

:iniciar
echo.
echo [4/4] Iniciando a API backend e a interface web...
for /f %%P in ('python app.py --porta-livre %PORTA_INICIAL%') do set "PORTA=%%P"
if not defined PORTA goto erro_porta
set "HOST=localhost"
if defined ARGS_REDE for /f %%I in ('python app.py --endereco') do set "HOST=%%I"
start "Schulz - API backend (porta %PORTA%)" cmd /k python app.py --porta %PORTA% %ARGS_REDE%
echo       Aguardando a API responder na porta %PORTA%...
python app.py --aguardar %PORTA%
if errorlevel 1 goto erro_api
start "" "http://%HOST%:%PORTA%"
echo       OK - navegador aberto em http://%HOST%:%PORTA%
echo.
echo ================================================================
echo  Aplicacao em execucao.
echo  Login: tecnico@local.test, signatario@local.test,
echo         admin@local.test ou auditor@local.test
echo  Senha: Local@12345
echo  Planilhas de teste: pasta "exemplos"
echo  Para encerrar: feche a janela "Schulz - API backend".
echo ================================================================
echo.
echo Esta janela pode ser fechada; a aplicacao continua rodando.
pause
exit /b 0

:sem_python
echo.
echo  ERRO: Python 3.10 ou superior nao foi encontrado no PATH.
echo  1. Baixe em https://www.python.org/downloads/
echo  2. Na instalacao, marque "Add python.exe to PATH".
echo  3. Feche esta janela e execute este arquivo novamente.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:erro_venv
echo.
echo  ERRO: nao foi possivel criar o ambiente virtual (.venv).
echo  Verifique se a pasta do projeto nao esta somente leitura.
pause
exit /b 1

:erro_pip
echo.
echo  ERRO: falha ao instalar as bibliotecas.
echo  Verifique a conexao com a internet e execute novamente.
pause
exit /b 1

:erro_porta
echo.
echo  ERRO: nao foi encontrada porta livre a partir de %PORTA_INICIAL%.
pause
exit /b 1

:erro_api
echo.
echo  ERRO: a API nao respondeu. Veja a mensagem na janela "Schulz - API backend".
pause
exit /b 1
