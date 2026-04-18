# Suggested Commands

## TTS Server
```bash
cd services/tts-server && python3 -m py_compile minimal_server.py && echo OK
cd services/tts-server && python3 minimal_server.py
cd services/tts-server && python3 -m pip install -r requirements-minimal.txt
```

## Creepy Brain
```bash
cd services/creepy-brain && pip install -e .
cd services/creepy-brain && python3 -m py_compile app/main.py && echo OK
cd services/creepy-brain && python3 -m pytest tests/ -v
```

## Type Checking
```bash
python3 -m mypy path/to/module.py --strict
```

## Git / System
```bash
git status
git diff
git log --oneline -10
```
