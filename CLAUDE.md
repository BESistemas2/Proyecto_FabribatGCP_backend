@AGENTS.md

## Reglas extra para Claude Code

Todo lo de AGENTS.md aplica igual. Esto se suma para trabajar en carriles de Orca.

### Carriles (Orca + Linear)

- La tarea viene de una issue de Linear (por ejemplo `TI-123`) o de `tareas/TNN.md`. Léela completa antes de empezar.
- Si la rama actual no es `main`, ya estás en un worktree creado por Orca: trabaja en esa rama. No crees ramas ni worktrees nuevos, aunque una skill (por ejemplo Superpowers) lo sugiera.
- Antes de escribir código, propón un plan corto (archivos a tocar, enfoque, pruebas) y espera aprobación.
- No hagas `git push` ni abras PR salvo que te lo pidan. El PR se abre con `gh pr create` cuando la tarea esté revisada.

### Entorno

- Windows con Git Bash. Usa rutas con `/` y `python -m pytest`, no `pytest` suelto.
- No tienes acceso a red ni a servidores en este repo (ver Reglas de seguridad). Si algo lo requiere, detente y pregunta.
