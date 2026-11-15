# Статус обчислень

Зроблено **45 з 52** прогонів (87 %). Датасет `ucu`, 13 сиквенсів, методи з `scripts/queue/methods.conf`.

Зібрано автоматично з гілок `runner-<host>` (`scripts/report/build_status.py`, Action `status.yml`). Не редагувати вручну.

## Зараз

| ПК | стан | останній звіт | прогонів зробив |
|---|---|---|---:|
| StarDestroyer | рахує seq03 · dpvo:mono · прогін 1 (з 2026-11-15T12:31:28) | 2026-11-15 12:31 +0200 | 40 |
| Teodor | ⚠ звітів нема понад поріг (ПК вимкнений або воркер завис) | 2026-11-13 18:49 +0200 | 5 |

## Сиквенси × методи

| # | seq | dpvo:mono (×3) | wheel_odom:odom (×1) |
|---:|---|---|---|
| 1 | seq01 | ✅ 3/3 Teodor | 0/1 |
| 2 | seq02 | 2/3 Teodor | 0/1 |
| 3 | seq03 | 0/3 ⏳StarDestroyer | 0/1 |
| 4 | seq04 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 5 | seq05 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 6 | seq06 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 7 | seq07 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 8 | seq08 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 9 | seq09 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 10 | seq10 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 11 | seq11 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 12 | seq12 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
| 13 | seq13 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
|  | **разом** | **35/39** | **10/13** |

Клітинка: готово/треба і хто рахував (кілька ПК: скільки прогонів кожен). ✅ усе готово, ⏳ зараз рахується, ✗N — N прогонів позначені невдалими (`.bad_run`).
«Звітів нема»: гілка ПК не оновлювалась понад 3 год.

Інші результати на гілках (не входять у таблицю):
- `dpvo:mono tum fr1_xyz`

_Зміст востаннє змінився: 2026-11-15 10:37 UTC_
