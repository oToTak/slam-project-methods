# eval/01 — venv

**Мета:** робоче середовище оцінки на Mac рівно за CONVENTIONS.md §5.

**Готово, коли:**
- у корені репо `requirements.txt` — дослівно з §5;
- `uv venv --python 3.12 ~/venvs/slam && uv pip install -r requirements.txt` проходить;
- `evo pkg --version` → 1.37.1; `evo_ape tum` відпрацьовує на парі тестових файлів (GT vs GT → ATE ≈ 0);
- `impl.md`: що встановлено, скільки зайняло, проблеми.

**Межі:** змінювати лише `requirements.txt`, цю папку завдання, `tasks/LOG.md`.
Версії пакетів не міняти. Якщо якийсь пакет не ставиться на macOS 12 Intel — «блокер» + текст для команди в `tasks/inbox/`.

**Читати:** `tasks/eval/README.md`, CONVENTIONS.md §5.

**Питання автору:** немає.
