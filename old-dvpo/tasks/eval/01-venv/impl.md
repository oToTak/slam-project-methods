# eval/01 — impl

**Статус:** закрито.

**Зроблено**
- `requirements.txt` у корені — дослівно з CONVENTIONS.md §5 (evo 1.37.1, rosbags 0.11.9, tqdm 4.70.1, pillow 13.0.0, numpy 2.5.4).
- `uv venv --python 3.12 ~/venvs/slam` (CPython 3.12.5) + `uv pip install -r requirements.txt`: 27 пакетів, ~17 с.
  Колеса `numpy==2.5.4` для macOS 12 Intel є — збірка з джерел не знадобилась (відповідь на «Відкрите» з README).

**Перевірка**
- `evo pkg --version` → 1.37.1.
- `evo_ape tum gt.txt gt.txt -as --t_max_diff 0.02` на синтетичному GT (1000 поз, спіраль): RMSE = 0.000000, max = 0.000000.

**Відхилення:** немає. **Питання автору:** немає.
