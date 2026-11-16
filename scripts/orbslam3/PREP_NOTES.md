# ORB-SLAM3 (`orbslam3`: `mono`, `mono_inertial`) - підготовка без збірки

Складено 16.11.2026. **Нічого не збирали, не ставили, не качали і не запускали.** Є лише код + тести на заглушках (чисті python-частини).
Реєстр `methods.conf`, `RUNBOOK.md`, `TASKS.md`, `setup_all_really_all.sh` **не змінені** - злиття робить лід.

## 1. Що лежить у гілці

| Файл | Що це |
|---|---|
| `scripts/setup/orbslam3.sh` | рецепт: `--check` / повна установка (ідемпотентно) / `--apt-only` / `--no-apt`; apt - ОДИН окремий крок |
| `scripts/orbslam3/env_orbslam3.sh` | змінні методу (піни, шляхи, `ORB3_*`); спільний `env.sh` не чіпали |
| `scripts/orbslam3/driver/orbslam3_tum.cc` + `CMakeLists.txt` | наш headless-драйвер (mono / mono_inertial), без viewer, без `usleep`, покадрова траєкторія |
| `scripts/orbslam3/run.sh` | run-скрипт за контрактом реєстру (`QUEUE_MODE` вибирає mono / mono_inertial) |
| `scripts/orbslam3/make_settings.py` | генерує камерний/IMU yaml з `configs/orbslam3/ucu_calib.json` (математика `T_ic` у docstring) |
| `scripts/orbslam3/convert_imu.py` | перевіряє й чистить `imu.txt` для драйвера (без перестановки осей, без зсуву часу) |
| `scripts/orbslam3/postprocess.py` | сирий вихід `SaveTrajectoryEuRoC` -> `traj_run<N>.txt` (TUM, час з `rgb.txt`) + `meta.yaml`; для inertial тіло IMU -> поза камери |
| `scripts/orbslam3/euroc_to_tum.py` | необов'язково: EuRoC `mav0/` -> TUM-папка для inertial sanity (нічого не качає) |
| `scripts/orbslam3/test_orbslam3.py`, `run_tests.sh` | тести на заглушках (секунди) + `bash -n` |
| `configs/orbslam3/ucu_calib.json`, `UCU_mono.yaml`, `UCU_mono_inertial.yaml` | калібрування (скопійоване з репо датасету) і згенеровані налаштування |

Перевірено в цій сесії (і лише це): `python -m py_compile` усіх `.py`, `bash -n` усіх `.sh`, 7 груп тестів (Windows python 3.12 і WSL Ubuntu-22.04, у WSL
ще й звірка з `~/src/ucu-slam-dataset/calibration`), `convert_imu.py` на справжньому `imu.txt` seq01. **C++ драйвер НЕ компілювали** (тому
може потребувати дрібних правок при першій збірці), рецепт НЕ запускали (крім `--check`, який чесно каже «не встановлено», код 1).

## 2. Рядки реєстру (додати в `scripts/queue/methods.conf` ЛИШЕ після приймання)

```
orbslam3 | mono | scripts/orbslam3/run.sh | cpu | 3
orbslam3 | mono_inertial | scripts/orbslam3/run.sh | cpu | 3
```
(`runs = 3` явно: ORB-SLAM3 недетермінований. Порожнє поле означало б «стільки, скільки в `--runs` воркера».)

## 3. Рядок-гачок у `setup_all_really_all.sh` (разом із реєстром, одним комітом)

Одразу після рядка `bash "$here/setup_all.sh" || { ... }` (крок 2/5):
```bash
bash "$here/orbslam3.sh" || todo "orbslam3: рецепт не вдався (метод буде пропущено, доки 'bash scripts/setup/orbslam3.sh --check' не пройде)"
```
Не додавати раніше: інакше повний сетап збиратиме ORB-SLAM3 усім ще до приймання. Для `START.bat` гачок не потрібен: `scripts/start.sh` (рядки ~109-116) сам запускає
рецепт відсутнього методу з реєстру, а `setup_all_really_all.sh --check` сам викликає `orbslam3.sh --check` для кожного методу реєстру.

## 4. Ручні кроки

* **sudo (лише крок apt):** `bash scripts/setup/orbslam3.sh --apt-only` (або повний запуск) спитає sudo-пароль **один раз**, і тільки якщо бракує пакетів. Пакети (з `APT_PKGS`):
  `build-essential cmake git pkg-config libopencv-dev libeigen3-dev libboost-serialization-dev libssl-dev libglew-dev libgl1-mesa-dev libegl1-mesa-dev libwayland-dev libxkbcommon-dev wayland-protocols libjpeg-dev libpng-dev`.
  Без sudo: поставити їх самому і запускати `bash scripts/setup/orbslam3.sh --no-apt` (або `ORB3_SKIP_APT=1`).
* Мережа: `git fetch` ORB-SLAM3 за комітом і `git clone` Pangolin v0.9.1 (з GitHub). Розміри **не виміряні**.
* **На ПК брата:** після `git pull` і `START.bat` він сам запустить рецепт; людині лишається ввести sudo-пароль (раз), якщо apt-пакетів бракує. Усе інше (Pangolin, ORB-SLAM3,
  драйвер) ставиться в `$HOME` без sudo. Час збірки на його ПК **не відомий**.
* Нічого в `/usr/local` не пишемо: Pangolin ставиться в `~/opt/orbslam3/pangolin`.

## 5. Чекліст «коли CPU вільний» (по порядку, точні команди; WSL Ubuntu-22.04)

```bash
cd /mnt/g/projects/slam/repo        # репо з цією гілкою (або після злиття)
export DATA_ROOT=/mnt/t/data        # де лежить ucu_tum/seq01 (перевір: ls $DATA_ROOT/ucu_tum/seq01/imu.txt)
```
0. Умови: черга не рахує (`bash scripts/start.sh --status`), вільна RAM (`free -g`): компіляція ORB-SLAM3 ~2 ГБ на потік (оцінка з практики, не виміряно тут).
1. Тести (секунди): `bash scripts/orbslam3/run_tests.sh`  -> очікувано `OK: 7 test groups passed`.
2. `bash scripts/setup/orbslam3.sh --check`  -> очікувано код 1 («не встановлено»).
3. sudo-крок: `bash scripts/setup/orbslam3.sh --apt-only`
4. Збірка (без sudo; лог поруч):
   ```bash
   mkdir -p $DATA_ROOT/logs
   ORB3_JOBS=4 bash scripts/setup/orbslam3.sh --no-apt 2>&1 | tee $DATA_ROOT/logs/orbslam3_setup_$(date +%Y%m%d_%H%M%S).log
   ```
   Якщо падає компіляція: читати **перший** `error:` у кінці логу. Відомі запасні важелі (жоден не перевірений тут):
   * `ORB3_CXX_STD=c++17 ORB3_FORCE=1 bash scripts/setup/orbslam3.sh --no-apt` (Pangolin v0.9 сам збирається в C++17);
   * Pangolin v0.8: `ORB3_PANGOLIN_TAG=v0.8 ORB3_PANGOLIN_SHORT=aff6883 ORB3_PANGOLIN_SRC=$HOME/src/Pangolin-v0.8 ORB3_FORCE=1 bash scripts/setup/orbslam3.sh --no-apt`
     (v0.8 = коміт aff6883 за сторінкою тегів GitHub).
5. `bash scripts/setup/orbslam3.sh --check` -> код 0. Далі `. scripts/setup/env.sh; . scripts/orbslam3/env_orbslam3.sh; $ORB3_BIN` -> має надрукувати usage (код 2): перевіряє, що лінкування живе.
6. **Sanity TUM fr1_xyz, mono** (дані: `bash scripts/data/fetch_tum.sh`, ~461 МБ - спершу дозвіл Теодора):
   ```bash
   NO_EVAL=1 QUEUE_MODE=mono bash scripts/orbslam3/run.sh tum fr1_xyz --run 1
   "$SLAM_VENV/bin/python" scripts/eval.py --method orbslam3 --mode mono --dataset tum --seq fr1_xyz --run 1 --dry-run
   ```
   Дивитись у лозі: рядок `ORB3_STATS ... state_hist=` (більшість кадрів у стані `2`), число поз ≈ число кадрів, ATE порівняти з відомим для fr1_xyz (DPVO: 1.1 см, `CLAUDE_teodor.md`;
   опубліковане значення ORB-SLAM3 для fr1_xyz **не знайдено в цій сесії: TODO(verify)**). Це ж перший тест на те, що без `usleep` трекінг не гірший (розділ 6, п. 3).
7. (необов'язково) Те саме з реальним часом: `ORB3_PACE=1 NO_EVAL=1 QUEUE_MODE=mono bash scripts/orbslam3/run.sh tum fr1_xyz --run 2` і порівняти ATE/покриття з п. 6.
8. **seq01 mono:** `NO_EVAL=1 QUEUE_MODE=mono bash scripts/orbslam3/run.sh ucu seq01 --run 1`, потім `eval.py ... --mode mono --dataset ucu --seq seq01 --run 1 --dry-run`.
   Дивитись: кількість поз проти 6532 кадрів, «There are N maps in the atlas» у лозі (у файл потрапляє лише найбільша мапа), `coverage`.
9. **seq01 mono_inertial:** `NO_EVAL=1 QUEUE_MODE=mono_inertial bash scripts/orbslam3/run.sh ucu seq01 --run 1`, `eval.py ... --mode mono_inertial ... --dry-run` (SE(3), `-a`).
   Якщо ініціалізація не відбувається або масштаб дивний -> абляція екстрінсиків (одна зміна, одна нова `run`):
   ```bash
   python3 scripts/orbslam3/make_settings.py --mode mono_inertial --ablation-use-t-ci --out /tmp/UCU_mono_inertial_tci.yaml
   ORB3_SETTINGS=/tmp/UCU_mono_inertial_tci.yaml NO_EVAL=1 QUEUE_MODE=mono_inertial bash scripts/orbslam3/run.sh ucu seq01 --run 9
   ```
   (run 9 - лише для порівняння, потім видалити `results/orbslam3/mono_inertial/ucu/seq01/traj_run9.txt` і не вносити в summary).
10. (необов'язково) EuRoC MH_01 для inertial sanity: дані **не качали**; розмір і джерело перевірити (T0.4). Конвертація: `python3 scripts/orbslam3/euroc_to_tum.py <MH_01_easy> $DATA_ROOT/euroc_tum/MH_01`,
    запуск `NO_EVAL=1 QUEUE_MODE=mono_inertial bash scripts/orbslam3/run.sh euroc MH_01 --run 1` (вихід - поза тіла IMU, бо GT EuRoC у тілі). Оцінка потребує перевірки: `eval.py` для `euroc`
    шукає дані в `ucu_tum` (див. розділ 6, п. 9), тому `run.sh` передає `--gt/--rgb` явно; чи цього досить - **TODO(verify)**.
11. Прогони 2 і 3 для seq01 обох режимів (недетермінованість): `QUEUE_MODE=<mode> bash scripts/orbslam3/run.sh ucu seq01 --run 2` (і 3). `NO_EVAL=1` лишати, поки не вирішено, що писати в `summary.csv`.
12. Додати рядки реєстру (п. 2) і гачок (п. 3), перевірити: `bash scripts/run_queue.sh --dry-run --from 1 --dir up --methods orbslam3 --runs 3` та `bash scripts/setup/setup_all_really_all.sh --check`
    (має показати `orbslam3 ok`).
13. Закомітити, Теодор пушить `main` -> брат підхоплює на наступному `START.bat`.

## 6. Що ВІДОМО (з першоджерел), а що `TODO(verify)`

**Відомо (перевірено читанням коду/файлів):**
* Пін ORB-SLAM3 = `4452a3c4ab75b1cde34e5505a36ec3f9edcdc4c4` (master HEAD, 2022-02-10, після v1.0 `0df83dd` від 2021-12-22) - підтверджено двічі (сторінка комітів і GitHub API).
  Пін Pangolin = тег `v0.9.1` = `7364b59` (сторінка тегів).
* API з вихідників на цьому коміті: `System(voc, settings, sensor, bUseViewer)`, `TrackMonocular(im, t, vector<IMU::Point>)`, `GetTrackingState()`, `SaveTrajectoryEuRoC` (**покадрова**, час у **наносекундах** ×1e9;
  кадри з втраченим трекінгом і не з найбільшої мапи пропускаються; перевірки «не для mono» немає), `Shutdown()` не чекає потоки. `SaveTrajectoryTUM` для mono взагалі відмовляється працювати.
* `inertial`: `SaveTrajectoryEuRoC` дає позу **тіла (IMU)** в системі першого keyframe, тому `postprocess.py` переводить її в позу камери `T_w_c = T_w_b * T_b_c`.
* `Settings.cc`: обов'язкові ключі й типи (float-ключі мають бути дійсними числами: `125.0`, не `125`; `IMU.T_b_c1` з `dt: f`); шум IMU ORB-SLAM3 множить на `sqrt(IMU.Frequency)` сам
  (`Tracking.cc`), тож у yaml йдуть безперервні щільності Kalibr як є.
* Екстрінсики: Kalibr `T_cam_imu` (`camchain-imucam.yaml`) = IMU->камера. У `calibration.yaml` ті самі числа названі `T_base_color` (назва інвертована, `CONVENTIONS.md`). `IMU.T_b_c1` = камера->тіло =
  `inverse(T_cam_imu)` = Kalibr `T_ic`; обчислене обернення збігається з надрукованим `T_ic` у `results-imucam.txt` (тест, точність 6e-9). Тіло = IMU (`T_base_imu = I`).
* Дані seq01 (виміряно зараз): `imu.txt` 27265 рядків, медіана кроку 7.9999 мс (≈125 Гц), максимум 16 мс, без нечислових і без зворотного часу, IMU починається на 0.119 с до першого кадру й
  закінчується на 0.033 с після останнього. Середнє прискорення на старті ≈ (-0.04, 0.18, -9.86): `az < 0` узгоджується з віссю z вниз (повертає Kalibr-обертання `T_ic`) і зі стандартним знаком специфічної сили.
* Час уже зсунутий у час base (14.9 мс) експортером - **не зсуваємо вдруге** (`CONVENTIONS.md` §3).
* Калібрування: усі цифри в `ucu_calib.json` збігаються з файлами репо датасету і з `configs/dpvo/calib_ucu.txt` (тести).

**`TODO(verify)` (не відомо, не вгадуємо):**
1. Чи збирається зв'язка Pangolin v0.9.1 + `-std=c++14` + GCC 11 + OpenCV 4.5.4 + Eigen 3.4 на цій машині. Ґрунтується на публічних звітах (sed `c++11`->`c++14` на 22.04, сигнатури помилок sigslot у
   Pangolin вимагають C++14); саме комбінацію з v0.9.1 у жодному джерелі не підтверджено. Назви опцій Pangolin (`BUILD_PANGOLIN_PYTHON` тощо) і змінна `Pangolin_LIBRARIES` у v0.9.1 не перевірені.
2. Чи компілюється `orbslam3_tum.cc` (не збирали). Числові значення станів трекінгу (`Tracking::OK` тощо) взято з `Tracking.h`.
3. Чи погіршує прогін **без `usleep`** результат (локальне картографування працює в окремому потоці): п. 6-7 чекліста порівнюють `ORB3_PACE=0` і `1`.
4. Чи правильний напрям екстрінсиків на ділі: обертання Kalibr майже симетричне, тож помилку видно лише в трансляції (~5 см); перевіряється абляцією (п. 9) і лише емпірично.
5. Шум IMU: щільності Kalibr можуть бути оптимістичними; **не змінювали**. Якщо ініціалізація нестабільна - окремий документований `run`.
6. `IMU.Frequency` = 125.0 номінальна; реальна 125.0016 Гц (з seq01).
7. Опубліковане еталонне ATE ORB-SLAM3 на TUM fr1_xyz (mono) і EuRoC MH_01 (inertial) для порівняння - не шукали.
8. Поведінка кількох мап: у файл потрапляє лише найбільша мапа атласу; після втрати трекінгу ORB-SLAM3 може почати нову мапу, і частина кадрів лишиться без пози. Це чесно відображається в `coverage`
   (за часом) і в кількості поз, але воркер позначить прогін `.bad`, якщо поз < `POSE_TOL`×кадрів (`run_queue.py`).
9. `eval.py` для `--dataset euroc` шукає дані в `ucu_tum` (рядок ~260); `run.sh euroc` обходить це явними `--gt/--rgb`, чи цього вистачає для `groundtruth`-парування - не перевірено.
10. Час/пам'ять/диск збірки й прогону: **не виміряно** (оцінок не вигадуємо).

## 7. Для ліда при злитті (спільні файли, які я не чіпав)

* `scripts/sync/common.sh` / `START_BUSY_PATTERN` за замовчуванням `run_queue[.]py|run_dpvo[.]py`: додати `|orbslam3_tum`, інакше `START.bat` не вважатиме ORB-SLAM3 зайнятістю.
* `scripts/eval.py` (~рядок 260): `euroc` мапиться в `ucu_tum` - або виправити, або лишити як є (sanity на EuRoC необов'язковий).
* `.gitattributes`: у корені немає `*.yaml/*.cc/*.md/*.json eol=lf`; я поклав локальні `.gitattributes` у `scripts/orbslam3/` і `configs/orbslam3/` (`* text eol=lf`).
* `TASKS.md` (рядок T3), `scripts/RUNBOOK.md`, `scripts/setup/README.md`: оновити після приймання; тут нічого з цього не редагувалось.
* Гілка: `worktree-agent-a2c906ee4e40f2161`; лише нові файли, конфліктів зі спільними файлами нема.

## 8. Джерела (прочитано 16.11.2026)

* ORB-SLAM3 @4452a3c: `src/System.cc`, `src/Settings.cc`, `src/Tracking.cc`, `include/System.h`, `include/Tracking.h`, `include/ImuTypes.h`, `CMakeLists.txt`, `build.sh`,
  `Examples/Monocular-Inertial/EuRoC.yaml`, `Examples/Monocular-Inertial/mono_inertial_euroc.cc` - https://github.com/UZ-SLAMLab/ORB_SLAM3 (сирі файли за комітом).
* Збірка на 22.04, C++14: upstream PR #567 (https://github.com/UZ-SLAMLab/ORB_SLAM3/pull/567, коментар про Ubuntu 22.04), issue #458 (помилки sigslot у Pangolin при C++11),
  форк https://github.com/guyuehome/ORB_SLAM3 (перехід на C++14, тест на 22.04), gist https://gist.github.com/bharath5673/4295e666cbe654a83226a2549a972c4f (`sed -i 's/++11/++14/g' CMakeLists.txt`, Eigen 3.4 ок),
  https://a1026302.hatenablog.com/entry/2024/10/24/155343 (залежності, помилки). Японський гайд для WSL2/22.04 з Pangolin 0.9.1 (https://elosove.com/?p=1077) за пошуком існує, але сторінка
  не відкрилась (503), тому його вміст не використано.
* Pangolin v0.9.1: `scripts/install_prerequisites.sh`, `CMakeLists.txt` (C++17 за замовчуванням, опції `BUILD_EXAMPLES/TOOLS/TESTS`), сторінка тегів - https://github.com/stevenlovegrove/Pangolin.
* Репо проєкту: `old-dvpo/CONVENTIONS.md` (екстрінсики, формат траєкторії), `scripts/queue/methods.conf` (контракт), `scripts/setup/dpvo.sh`, `scripts/wheel_odom/*`, `scripts/start.sh`.
