# Локальне GPU-середовище для DPVO (WSL2) — рецепт

Все, що поставлено на головному ПК, записано скриптами тут. Той самий рецепт запускається на ПК брата
(RTX 3060 12 GB) **однією командою** `setup_all.sh`. Образ (`wsl --export`) — окремий крок пізніше (фаза 8 `PLAN_local.md`).

Що буде встановлено (усе у `~`, окрім apt-пакетів і CUDA): `cmake ninja-build unzip build-essential`,
CUDA-toolkit **12.1** (лише для збірки; драйвер береться з Windows), `uv`, venv `~/venvs/slam`
(Python 3.12, `evo==1.37.1`, `rosbags`, `hf`), venv `~/venvs/dpvo` (Python 3.10, torch 2.3.1/cu121, numpy 1.26.4),
`~/src/DPVO` @ `0ac95b6` зі зібраними CUDA-розширеннями (архітектури `7.5;8.6;8.9`, без `-march=native`) і вагами `dpvo.pth`.

## A. Головний ПК (`TEODOR`) — вже готово
Перевірка стану (≈10 с):
```bash
wsl -d Ubuntu-22.04
cd /mnt/t/my_repos/slam-project-methods
bash scripts/setup/10_verify.sh        # має закінчитись "ALL OK"
```
Рекомендовано (робить Теодор сам, Windows-налаштування): скопіювати `scripts/setup/wslconfig.example`
у `%UserProfile%\.wslconfig`, потім у PowerShell `wsl --shutdown`. Зараз WSL бачить лише ~7 GB ОЗП;
збірка CUDA-розширень на 7 GB йде в 1–2 потоки (рецепт сам підбирає `MAX_JOBS` за вільною ОЗП).

Приймальний тест (качає seq01 6.5 GB, якщо ще нема; DPVO ≈ 1–1.5 год на GTX 1660 SUPER):
```bash
bash scripts/accept_seq01.sh           # у кінці: ACCEPTED / NOT ACCEPTED (ATE ≈ 2.34 м ± 0.30, coverage ≥ 0.99)
```

## B. ПК брата — по кроках
**0. Windows (один раз, адмін, потрібне перезавантаження).** У PowerShell «від імені адміністратора»:
```powershell
wsl --install -d Ubuntu-22.04
```
Перезавантажити ПК, відкрити «Ubuntu 22.04», створити Linux-користувача й пароль. Оновити драйвер NVIDIA **у Windows**
(WSL драйвер не ставить). Перевірити в Ubuntu: `nvidia-smi` показує RTX 3060. Вільного місця на диску з WSL — ≥ 50 GB.
Бажано створити `%UserProfile%\.wslconfig` за шаблоном `wslconfig.example` (підігнати `memory`, `processors`, шлях swap-файлу).

**1. Отримати скрипти.** На головному ПК (у WSL, після коміту змін): `bash scripts/setup/make_bundle.sh` → файл
`slam-local-<hash>.tar.gz` (кілька десятків KB) у `T:\slam-data\bundle\` → завантажити на Google Диск → скачати на ПК брата.
У Ubuntu брата (файл лежить, наприклад, у `C:\Users\<ім'я>\Downloads`):
```bash
tar -xzf /mnt/c/Users/<ім'я>/Downloads/slam-local-<hash>.tar.gz -C ~
cd ~/slam-local
```
(Репо тримати у Linux-домашній папці, не на `/mnt/c` — швидше. Дані підуть у `~/slam-data`, якщо немає диска `T:`;
інший шлях: `export DATA_ROOT=/mnt/d/slam-data`.)

**2. Одна команда встановлення** (≈ 30–60 хв, спитає пароль sudo один раз; повторний запуск безпечний):
```bash
bash scripts/setup/setup_all.sh
```
Лог: `$DATA_ROOT/logs/setup_all_*.log`. У кінці — `ALL OK` (перевірка GPU, nvcc 12.1, evo 1.37.1, імпорт DPVO, ваги, тести `eval.py`).

**3. HF-логін** (один раз; токен вводить людина, краще fine-grained read-only; умови датасету мають бути прийняті для цього акаунта):
```bash
hf auth login
```
**4. Приймальний тест:**
```bash
bash scripts/accept_seq01.sh
```
Очікування: `ACCEPTED`. Час на RTX 3060 буде інший (не помилка) — запишіть `wall` і `VRAM` з `meta.yaml` у нотатки.
Результат — `results/summary.csv` (+ `summary_extra.csv`) і `results/dpvo/mono/ucu/seq01/` — повернути на Диск.
Після завершення на чужому ПК: `hf auth logout`.

## C. Що де лежить
| Скрипт | Призначення |
|---|---|
| `setup_all.sh` | усе встановлення (кроки 01→02→04→05 + TUM + перевірка) |
| `01_system_root.sh` | apt-пакети + CUDA 12.1 (sudo) |
| `02_python.sh` | `uv`, `~/venvs/slam` (evo 1.37.1), `hf` |
| `04_dpvo_src.sh`, `05_dpvo_env.sh` | DPVO @ `0ac95b6` + Eigen 3.4.0; venv, збірка, ваги |
| `10_verify.sh` | швидка перевірка середовища (exit 0 = все гаразд) |
| `env.sh` | спільні змінні (`DATA_ROOT`, venv-и, `DPVO_DIR`) — перевизначаються з оточення |
| `../data/fetch_ucu.sh N` | завантаження `sequence-N` з HF + офіційний `export_tum.py`; таймінги → `$DATA_ROOT/logs/fetch_timings.csv` |
| `../data/fetch_tum.sh` | TUM `fr1_xyz` |
| `../dpvo/run.sh tum\|ucu SEQ` | прогін DPVO (stride 1) + `eval.py` → рядок у `results/summary.csv` |
| `../eval.py` | спільна оцінка: ATE (`-as`, `--t_max_diff 0.02`), coverage за часом, дрейф 50/100 м, `tracking_lost` |
| `../accept_seq01.sh` | приймальний тест seq01 |
| `make_bundle.sh` | архів скриптів для Диска |

Дані лежать **поза git** у `$DATA_ROOT` (головний ПК: `T:\slam-data`): `ucu/sequence-N/*.mcap`, `ucu_tum/seqNN/`, `tum/fr1_xyz/`.
Після експорту сирий `.mcap` можна видалити (`rm -rf $DATA_ROOT/ucu/sequence-N`) — він займає 6–20 GB.

## D. Типові проблеми
| Симптом | Що робити |
|---|---|
| `nvidia-smi` у WSL не бачить GPU | оновити драйвер NVIDIA у Windows; `wsl --shutdown`, перезапуск |
| `Killed` / код 137 при збірці CUDA | мало ОЗП: `.wslconfig` (`memory=`), або `MAX_JOBS=1 bash scripts/setup/05_dpvo_env.sh` |
| `operation timed out` при завантаженні torch | повторити `setup_all.sh` (є `UV_HTTP_TIMEOUT=300`, докачує) |
| `No module named 'dpvo.loop_closure'` | `pip install .` не включає його; `run_dpvo.py` додає `~/src/DPVO` у `sys.path` — запускати через `scripts/dpvo/run.sh` |
| `hf` каже «не залогінений» | `hf auth login` (токен вводиш сам) |
| ATE на TUM ≫ 1 см | не йти на УКУ: перевірити калібрування, версії з `10_verify.sh` |
