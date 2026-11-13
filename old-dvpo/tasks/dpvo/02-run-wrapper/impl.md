# dpvo/02 — impl

**Статус:** виконано (GPU-частина `run_slam` ще не запускалась — це буде в dpvo/03 у Colab).

## Що зроблено
- `scripts/dpvo/run.sh <tum|ucu> <seq>` → `scripts/dpvo/run_dpvo.py`. Шляхи даних: `data/tum/<seq>/`, `data/ucu_tum/<seq>/`; DPVO береться з `$DPVO_DIR` (типово `/content/DPVO`: `config/default.yaml`, `dpvo.pth`).
- `run_dpvo.py`: кадри й таймстемпи з `rgb.txt` (секунди, як є), `stride=1`; `DPVO.terminate()` → TUM-файл (9 знаків, без заголовка, `x y z qx qy qz qw`), `traj_run<N>.txt` з наступним вільним N; `meta.yaml` за шаблоном §4.
- `meta.yaml`: `commit` = sha нашого репо; `config` = шлях до calib-файлу (`configs/dpvo/calib_*.txt`); `coverage` за часом (§5); `tracking_lost`; `notes` (коміт DPVO, stride, кількість кадрів, чи був брудний репо, текст помилки).
- `configs/dpvo/calib_ucu.txt`, `configs/dpvo/calib_tum_fr1.txt` — значення з README напрямку, один рядок (сумісно з форматом DPVO). Для `tum`: undistort + обрізка 8 px зверху/знизу, 16 px з боків (cx −16, cy −8), як в `evaluate_tum.py`; для всіх — обрізка до кратного 16.
- Чисті функції (парсинг `rgb.txt`, запис траєкторії/meta, coverage, номер прогону) не імпортують torch/dpvo; DPVO імпортується ліниво у `run_slam`.

## Як перевірено (Mac, без GPU)
`uv run --no-project --with numpy --with pyyaml python scripts/dpvo/test_run_dpvo.py` → `OK`. Тест на синтетичних позах (300 кадрів): формат рядка (8 полів, 9 знаків, без `#`), три прогони поспіль дають `traj_run1/2/3` без перезапису, coverage = 0.50 для половини траєкторії, `meta.yaml` читається `yaml.safe_load`, `tracking_lost` (немає файлу траєкторії), NaN-пози → lost. Окремо перевірено `frame_stream` на синтетичному 640×480 (opencv): TUM → 464×608, cx/cy = 302.6/247.3; UCU → 480×640 без змін. `run.sh` без даних дає зрозумілу помилку.

## Відхилення / рішення
- `meta.yaml` пишеться вручну (без PyYAML), `notes` — у подвійних лапках (JSON-рядок, валідний YAML).
- Збій/NaN → `tracking_lost: true`, `coverage: 0.00`, файлу траєкторії немає; `meta.yaml` описує останній прогін (§4).
- Поки DPVO не має власного індикатора втрати треку: «lost» = виняток або <2 пози/NaN. Часткову втрату видно лише з coverage.
- `tasks/LOG.md` не чіпав (за вказівкою).

## Питання автору
немає

## Правки після рев'ю
- `git_commit()`: `git status --porcelain --untracked-files=no`, щоб нові `results/` не робили репо «брудним».
- `DPVO.terminate()` повертає `np.array(self.tlist, dtype=np.float64)` зі значень, які ми подали (не float32/long); додано `check_tstamps` (помилка, якщо max|Δ| ≥ 1e-6 с при однаковій довжині) + тест.
