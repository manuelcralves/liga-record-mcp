@echo off
rem A rotina diaria, como a tarefa agendada a corre. Sem janelas, sem browser.
rem
rem Existe por duas razoes. A primeira e que uma tarefa agendada nao tem sitio
rem nenhum para onde escrever: se o python rebentar, a mensagem morre com ele e
rem o agendador so guarda um numero. A saida em bruto fica em data\routine.err,
rem que e reescrito a cada corrida -- e sempre a ultima.
rem
rem A segunda e que passa a linha de comandos para aqui, onde se le e se muda,
rem em vez de a deixar enterrada no agendador. Apontada direita ao python.exe,
rem a tarefa morria com 0xC000013A sem deixar rasto nenhum.
rem
rem O resumo legivel vai para data\routine.log, acumulado.

setlocal
cd /d "%~dp0.."

rem ---------------------------------------------------------------------------
rem O ledger tem dois escritores: este PC e o trabalho agendado no GitHub, que
rem regista as previsoes nos dias em que o PC esta desligado. Ambos escrevem
rem data\projections.json, e se este correr sem ter puxado o que o GitHub ja
rem registou, escreve a mesma jornada uma segunda vez -- e um conflito num JSON
rem e uma chatice a desfazer a mao.
rem
rem Puxar primeiro resolve o caso normal. O --ff-only e de proposito: recusa em
rem vez de inventar uma juncao, e passa bem mesmo com as paginas por commitar,
rem porque o GitHub nunca lhes toca. Se recusar, corre na mesma e avisa: o
rem trabalho na nuvem pode estar bloqueado do lado do Record, e nesse caso este
rem PC e o unico que regista alguma coisa.
rem ---------------------------------------------------------------------------
where git >nul 2>&1
if errorlevel 1 goto :correr
git fetch --quiet origin
if errorlevel 1 goto :correr
git pull --ff-only --quiet
if errorlevel 1 goto :divergiu
goto :correr

:divergiu
echo === o ledger local nao sincronizou com o GitHub >> "data\routine.log"
echo     ou tens commits por enviar, ou os dois escreveram a mesma jornada >> "data\routine.log"
echo     ve com: git status  e  git log --oneline origin/main..HEAD >> "data\routine.log"
echo. >> "data\routine.log"

:correr
".venv\Scripts\python.exe" -X utf8 "scripts\routine.py" --quiet --log "data\routine.log" > "data\routine.err" 2>&1
set ERRO=%ERRORLEVEL%

if %ERRO% NEQ 0 (
  echo === falhou com %ERRO% >> "data\routine.log"
  echo     o detalhe em bruto esta em data\routine.err >> "data\routine.log"
  echo. >> "data\routine.log"
)

endlocal & exit /b %ERRO%
