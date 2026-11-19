# Статус обчислень

Зроблено **93 з 130** прогонів (72 %). Датасет `ucu`, 13 сиквенсів, методи з `scripts/queue/methods.conf`.

Зібрано автоматично з гілок `runner-<host>` (`scripts/report/build_status.py`, Action `status.yml`). Не редагувати вручну.

## Зараз

| ПК | стан | останній звіт | прогонів зробив |
|---|---|---|---:|
| StarDestroyer | рахує seq07 · orbslam3:mono · прогін 3 (з 2026-11-19T09:51:39) | 2026-11-19 09:52 +0200 | 88 |
| Teodor | ⚠ звітів нема понад поріг (ПК вимкнений або воркер завис) | 2026-11-13 18:49 +0200 | 5 |

## Сиквенси × методи

| # | seq | dpvo:mono (×3) | wheel_odom:odom (×1) | dpvslam:mono (×3) | orbslam3:mono (×3) |
|---:|---|---|---|---|---|
| 1 | seq01 | ✅ 3/3 Teodor | ✅ 1/1 StarDestroyer | 0/3 | 0/3 |
| 2 | seq02 | ✅ 3/3 StarDestroyer 1 + Teodor 2 | ✅ 1/1 StarDestroyer | 0/3 | 0/3 |
| 3 | seq03 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | 0/3 | 0/3 |
| 4 | seq04 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | 0/3 | 0/3 |
| 5 | seq05 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | 0/3 | 0/3 |
| 6 | seq06 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | 0/3 | 0/3 |
| 7 | seq07 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | ✅ 3/3 StarDestroyer | 2/3 StarDestroyer ⏳StarDestroyer |
| 8 | seq08 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | ✅ 3/3 StarDestroyer | ✅ 3/3 StarDestroyer |
| 9 | seq09 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | ✅ 3/3 StarDestroyer | ✅ 3/3 StarDestroyer |
| 10 | seq10 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | ✅ 3/3 StarDestroyer | ✅ 3/3 StarDestroyer |
| 11 | seq11 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | ✅ 3/3 StarDestroyer | ✅ 3/3 StarDestroyer |
| 12 | seq12 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | ✅ 3/3 StarDestroyer | ✅ 3/3 StarDestroyer |
| 13 | seq13 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer | ✅ 3/3 StarDestroyer | ✅ 3/3 StarDestroyer |
|  | **разом** | **39/39** | **13/13** | **21/39** | **20/39** |

Клітинка: готово/треба і хто рахував (кілька ПК: скільки прогонів кожен). ✅ усе готово, ⏳ зараз рахується, ✗N — N прогонів позначені невдалими (`.bad_run`).
«Звітів нема»: гілка ПК не оновлювалась понад 3 год.

Інші результати на гілках (не входять у таблицю):
- `dpvo:mono tum fr1_xyz`
- `dso:mono ucu (нема в methods.conf)`
- `openvins:mono_inertial ucu (нема в methods.conf)`
- `orbslam3:mono_inertial ucu (нема в methods.conf)`

_Зміст востаннє змінився: 2026-11-19 07:57 UTC_
