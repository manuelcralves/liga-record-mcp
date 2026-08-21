@echo off
rem Sincronizar: puxa os dados, refaz as paginas, e abre a primeira.
rem
rem O cron diario ja faz isto sozinho. Isto e para quando queres os numeros ao
rem minuto -- depois de uma jornada fechar, por exemplo -- em vez de esperar
rem pela hora marcada.
rem
rem Correr isto varias vezes seguidas nao faz mal nenhum: cada passo do routine
rem recusa em vez de fazer a coisa errada, e uma execucao que nao muda nada e o
rem resultado normal.
rem
rem Faz um atalho para este ficheiro no ambiente de trabalho e tens o botao.

setlocal
cd /d "%~dp0.."

echo.
echo   A sincronizar...
echo.

".venv\Scripts\python.exe" -X utf8 "scripts\routine.py" --quiet --log "data\routine.log"
set ERRO=%ERRORLEVEL%

echo.
if %ERRO% NEQ 0 (
  echo   Alguma coisa falhou. O detalhe fica em data\routine.log
  echo.
  pause
  exit /b %ERRO%
)

echo   Feito. A abrir a pagina.
start "" "docs\private\index.html"
endlocal
