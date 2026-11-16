# Локальне GPU-середовище для DPVO (WSL2) — рецепт

> Нова людина або перший раз? Йди за **[GUIDE_with_ai.md](GUIDE_with_ai.md)** — покроковий алгоритм для роботи разом із нейронкою (де взяти архів, що вводити, що слати при помилці). Нижче — довідка.

Все, що поставлено на головному ПК, записано скриптами тут. Той самий рецепт запускається на ПК брата
(RTX 3060 12 GB) **однією командою** `setup_all_really_all.sh` (вона викликає `setup_all.sh`, потім вхід у Hugging Face, тести й план черги; один і той самий скрипт для обох ПК). Образ (`wsl --export`) — окремий крок пізніше (фаза 8 `PLAN_local.md`).

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
Той самий загальний скрипт, що й на ПК брата, можна запустити й тут (нічого не перевстановлює, лише перевіряє, тестує, показує план черги):
```bash
bash scripts/setup/setup_all_really_all.sh
```
Рахує на головному ПК не ця робоча копія, а окремий клон `~/slam-runner` (один раз: `bash scripts/report/push_status.sh --token`,
потім `bash scripts/sync/migrate_to_git.sh --pc main`; деталі — [`../RUNBOOK.md`](../RUNBOOK.md)). Далі — черга сиквенсів, розділ **E**.

## B. ПК брата — по кроках
Усі команди з кроку 1 і далі — **в терміналі Ubuntu** (не в PowerShell), крім тих, де вказано PowerShell.

**0. Windows (один раз, адмін, потрібне перезавантаження).**
- У BIOS має бути ввімкнена віртуалізація (SVM / Intel VT-x) — на ігрових ПК зазвичай уже.
- PowerShell «від імені адміністратора»:
```powershell
wsl --install -d Ubuntu-22.04
wsl --update
wsl --set-default-version 2
```
- Перезавантажити ПК, відкрити «Ubuntu 22.04», створити Linux-користувача й пароль.
- Оновити драйвер NVIDIA **у Windows** (у WSL драйвер не ставиться). У Ubuntu `nvidia-smi` має показати RTX 3060.
- Вільного місця на диску з WSL — ≥ 50 GB.

**1. Отримати код** — клон GitHub `main` у `~/slam-runner` (репо публічне; bundle більше не потрібен). У Ubuntu:
```bash
git clone -b main https://github.com/oToTak/slam-project-methods.git ~/slam-runner
bash ~/slam-runner/scripts/sync/migrate_to_git.sh --pc brother     # позначає клон, runner.conf, START.bat на робочий стіл; нічого не ставить
cd ~/slam-runner
cp scripts/setup/wslconfig.example "/mnt/c/Users/<ім'я>/.wslconfig"
```
Відкрити `C:\Users\<ім'я>\.wslconfig` у Блокноті, виставити `memory` (~70 % ОЗП), `processors`, шлях swap-файлу;
у PowerShell `wsl --shutdown`, знову відкрити Ubuntu, `cd ~/slam-runner`. **Зробити це до кроку 2** — від ОЗП залежить збірка CUDA.
(ПК, що вже рахував зі старого bundle `~/slam-local`: лише перехід, «Перший раз» у [`../RUNBOOK.md`](../RUNBOOK.md).)

Репо тримати у Linux-домашній папці, не на `/mnt/c` (там повільно). Дані підуть у `~/slam-data` (на `/mnt/d/...` теж повільно:
9p-диск, а `.mcap` 6–20 GB). Інший шлях — лише якщо потрібно, і тоді постійно: `echo 'export DATA_ROOT=/шлях' >> ~/.bashrc`.

**2. Одна команда встановлення** (≈ 30–60 хв; sudo-пароль один раз; повторний запуск безпечний):
```bash
bash scripts/setup/setup_all_really_all.sh
```
Вона по черзі: перевіряє машину (ОЗП, місце, GPU) → `setup_all.sh` (apt + CUDA, evo, збірка DPVO, ваги, TUM, перевірка) →
просить вхід у Hugging Face (**токен вводиш сам**; краще fine-grained read-only; умови датасету мають бути прийняті для цього акаунта;
на питання «add token as git credential?» відповісти `n`) і перевіряє доступ до датасету без завантаження → тести (`run_dpvo`, черга на заглушках)
→ показує план черги й друкує команди запуску. Лог: `~/slam-data/logs/setup_really_all_*.log` (і `setup_all_*.log`).
Якщо обірвалось — запустити ту саму команду ще раз. У кінці має бути `ALL OK` від `setup_all.sh` і жодного `[warn]`.
Якщо якась перевірка `[FAIL]` — `bash scripts/setup/10_verify.sh` покаже, яка саме.
(Лише вхід у HF, якщо треба окремо: `~/.local/bin/hf auth login`.)

**3. Приймальний тест** — тепер необов'язковий окремий крок: перший сиквенс черги (seq01 у головного ПК) і є перевіркою. Окремо:
```bash
bash scripts/accept_seq01.sh        # або: bash scripts/setup/setup_all_really_all.sh --accept
```
Очікування в кінці: `ACCEPTED` (ATE ≈ 2.34 м ± 0.30, coverage ≥ 0.99). Час інший — не помилка; `wall` і VRAM — у `results/dpvo/mono/ucu/seq01/meta.yaml` (`notes`).

**4. Запустити чергу** — `START.bat` на робочому столі (розділ **E** нижче і [`../RUNBOOK.md`](../RUNBOOK.md)).

**5. Результати** самі їдуть на GitHub у гілку `runner-<хост>` (потрібен токен: `bash scripts/report/push_status.sh --token`, вводиш сам).
Локально — `~/slam-data/results/` (`summary_<хост>.csv`, `summary_extra_<хост>.csv`, `dpvo/...`) і `~/slam-data/logs/fetch_timings.csv`.
Після завершення на чужому ПК: `~/.local/bin/hf auth logout`.

## E. Черга сиквенсів (`scripts/run_queue.sh`)
Одна програма для обох ПК: бере сиквенси **з одного кінця** відрізка 1…13. Для кожного: завантажує `.mcap` → експорт → усі (метод × прогін) →
перевірка кількості поз → `eval.py` → рядок у `$RESULTS_ROOT/summary_<хост>.csv` → видаляє `.mcap` й експорт (лише коли все пораховано). На диску одночасно один сиквенс.

**Звичайний запуск на обох ПК — `START.bat`** → `~/slam-runner/scripts/start.sh`: `git pull --ff-only` → налаштування ПК
(`~/.config/slam/runner.conf`: `START_AT=1|13`, `DATA_BUDGET_GB` (передається воркеру через оточення), `KEEP_DATA=no`, `RUNS`, `METHODS`, `REPORT_EVERY`) → `setup_all_really_all.sh --check`
(+ рецепт `scripts/setup/<метод>.sh` для відсутнього методу; не встановився — цього разу без нього) → входи HF/GitHub → `--status` → черга.
`start.sh --check` — кроки 1–5 без встановлення й запуску; `start.sh --status` — лише таблиця (`STATUS.bat`). Порядок для людей — [`../RUNBOOK.md`](../RUNBOOK.md).

Воркер рахує лише в клоні `~/slam-runner` (позначений файлом `.git/slam-runner`), і його результати лежать **поза git** у
`$RESULTS_ROOT = $DATA_ROOT/results`, тож `git pull` їх не чіпає. У робочій копії для розробки `RESULTS_ROOT` = `<repo>/results`, як і раніше
(`env.sh`; `run_queue.sh`, `dpvo/run.sh`, `eval.py`, `push_status.sh`, `accept_seq01.sh` беруть його звідти).

Те саме руками (те, що START.bat викликає на кроці 6):
```bash
# головний ПК (1, 2, 3, …)
cd ~/slam-runner && bash scripts/run_queue.sh --from 1 --dir up --methods all --runs 3
# ПК брата (13, 12, 11, …), бюджет диска 60 ГБ
cd ~/slam-runner && DATA_BUDGET_GB=60 bash scripts/run_queue.sh --from 13 --dir down --methods all --runs 3
```
- **Перервати:** `Ctrl-C` (один раз; готові прогони лишаються, незавершений викидається). **Продовжити:** та сама команда — готове пропускається.
- **Де я:** `bash scripts/run_queue.sh --status` — таблиця 13 позицій (готово / в роботі / частково / чекає / ПЕРЕВІР) і рядок «Ти тут: N»
  та «Готово підряд від твого кінця: …» — це Теодор і брат повідомляють одне одному.
- **Межа зустрічі:** коли дізнались, де інший (скажімо, він на 8): `bash scripts/run_queue.sh --set-stop 8` — «не братиму seq08 і далі в бік руху».
  Працює й під час роботи (воркер перечитує межу перед кожним прогоном); зняти: `--set-stop none`. Можна й одразу: `--stop-before 8` у команді запуску.
- **План без запуску:** `--dry-run` (які сиквенси, скільки місця треба, скільки качати). **Один сиквенс:** `--only N`. **Не видаляти дані:** `--keep-data`.
- **Методи:** `--methods dpvo` (за замовчуванням), кілька через кому або `all`. Реєстр методів — `scripts/queue/methods.conf`
  (новий метод = один рядок + його `run.sh`; контракт описано в самому файлі).
- **Номери прогонів** `1…R` фіксовані: прогін N сиквенсу — завжди `traj_runN.txt`, тож два ПК не плутаються, якщо беруть різні сиквенси.
  Готовим вважається прогін, для якого є `.done_run{R}` у `$RESULTS_ROOT/<метод>/mono/ucu/seqNN/` або рядок у будь-якому `$RESULTS_ROOT/summary*.csv`.
- **Таймаут** прогону 12 год (`--timeout-h`). Логи: `~/slam-data/logs/queue_<хост>_seqNN_<час>.log` (весь сиквенс) і `…_<метод>_run<R>.log` (прогін).
- Якщо прогін упав — дані цього сиквенсу лишаються для розбору, черга йде далі; два сиквенси поспіль із помилками → зупинка (схоже на проблему середовища).
  Мало місця / нема HF-входу / не вдалось завантажити → зупинка з поясненням, нічого не видаляється.
- Статус `ПЕРЕВІР` = прогін завершився, але поз менше, ніж кадрів (поріг 98 %); рядок у `summary` є, дані лишено, маркер `.bad_run{R}`. Розбирається вручну.
- Час: seq01 на GTX 1660 SUPER виміряно 88 хв за прогін; довші сиквенси — пропорційно довше (оцінка). Швидкість RTX 3060 ще не виміряна.

Перевірка самої черги без GPU і без завантажень (заглушки): `bash scripts/setup/run_queue_tests.sh`.

## C. Що де лежить
| Скрипт | Призначення |
|---|---|
| `../windows/START.bat`, `STATUS.bat` | **одна кнопка для обох ПК** → `../start.sh` / `../start.sh --status` у `~/slam-runner` (старі `START_MAIN/START_SLAM/STATUS_*.bat`, `main_*.sh`, `brother_*.sh` — обгортки над ними) |
| `../start.sh` | оновлення коду → налаштування ПК → перевірка встановлення → входи → статус → черга (`--check`, `--status`, `--no-pull`) |
| `../sync/migrate_to_git.sh` | одноразовий перехід ПК на `~/slam-runner` (`--pc main\|brother`, `--dry-run`); старе не видаляє |
| `../sync/common.sh`, `../sync/test_start.sh` | спільні функції (токен для git, `runner.conf`); тести `start.sh` і переходу на заглушках |
| `setup_all_really_all.sh` | **один скрипт для обох ПК:** перевірка машини → `setup_all.sh` → вхід у HF → тести → план черги. `--check`: лише що встановлено (нічого не ставить) |
| `dpvo.sh` | рецепт методу `dpvo`: `--check` (швидка перевірка) / без аргументів — встановлення (= `setup_all.sh`). Шаблон для інших методів |
| `dpvslam.sh` | рецепт методу `dpvslam` (DPV-SLAM = DPVO + близький loop closure `LOOP_CLOSURE True`): `--check` / без аргументів лише докидає pip-пакети (`pypose numba scipy einops`) у venv DPVO, без sudo і збірки; тест: `run_dpvslam_tests.sh` |
| `setup_all.sh` | усе встановлення (кроки 01→02→04→05 + TUM + перевірка) |
| `../run_queue.sh` | черга сиквенсів з одного кінця (`--from/--dir/--stop-before/--status`), код у `../queue/run_queue.py` |
| `../queue/methods.conf` | реєстр методів черги: метод → режим → `run.sh` → gpu/cpu |
| `run_queue_tests.sh` | тести черги на заглушках (без GPU й завантажень) |
| `01_system_root.sh` | apt-пакети + CUDA 12.1 (sudo) |
| `02_python.sh` | `uv`, `~/venvs/slam` (evo 1.37.1), `hf` |
| `04_dpvo_src.sh`, `05_dpvo_env.sh` | DPVO @ `0ac95b6` + Eigen 3.4.0; venv, збірка, ваги |
| `10_verify.sh` | швидка перевірка середовища (exit 0 = все гаразд) |
| `env.sh` | спільні змінні (`DATA_ROOT`, `RESULTS_ROOT`, `SLAM_RUNNER_DIR`, venv-и, `DPVO_DIR`) — перевизначаються з оточення |
| `../data/fetch_ucu.sh N` | завантаження `sequence-N` з HF + офіційний `export_tum.py`; таймінги → `$DATA_ROOT/logs/fetch_timings.csv` |
| `../data/fetch_tum.sh` | TUM `fr1_xyz` |
| `../dpvo/run.sh tum\|ucu SEQ [--run R]` | прогін DPVO (stride 1) + `eval.py` → рядок у `$RESULTS_ROOT/summary.csv` (черга викликає з `NO_EVAL=1` і оцінює сама в `summary_<хост>.csv`) |
| `../eval.py` | спільна оцінка: ATE (`-as`, `--t_max_diff 0.02`), coverage за часом, дрейф 50/100 м, `tracking_lost` |
| `../accept_seq01.sh` | приймальний тест seq01 |
| `make_bundle.sh` | архів скриптів для Диска (застарів: код тепер іде через `git clone`/`git pull`) |

Дані лежать **поза git** у `$DATA_ROOT` (головний ПК: `T:\slam-data`): `ucu/sequence-N/*.mcap`, `ucu_tum/seqNN/`, `tum/fr1_xyz/`.
Після експорту сирий `.mcap` можна видалити (`rm -rf $DATA_ROOT/ucu/sequence-N`) — він займає 6–20 GB. Черга видаляє `.mcap` й експорт сама, коли всі прогони сиквенсу готові.

## D. Типові проблеми
| Симптом | Що робити |
|---|---|
| `nvidia-smi` у WSL не бачить GPU | оновити драйвер NVIDIA у Windows; `wsl --shutdown`, перезапуск |
| `Killed` / код 137 при збірці CUDA | мало ОЗП: `.wslconfig` (`memory=`), або `MAX_JOBS=1 bash scripts/setup/05_dpvo_env.sh` |
| `operation timed out` при завантаженні torch | повторити `setup_all.sh` (є `UV_HTTP_TIMEOUT=300`, докачує) |
| `No module named 'dpvo.loop_closure'` | `pip install .` не включає його; `run_dpvo.py` додає `~/src/DPVO` у `sys.path` — запускати через `scripts/dpvo/run.sh` |
| `hf` каже «не залогінений» | `hf auth login` (токен вводиш сам) |
| ATE на TUM ≫ 1 см | не йти на УКУ: перевірити калібрування, версії з `10_verify.sh` |
