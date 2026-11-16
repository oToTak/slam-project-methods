# Статус обчислень

Зроблено **52 з 52** прогонів (100 %). Датасет `ucu`, 13 сиквенсів, методи з `scripts/queue/methods.conf`.

Зібрано автоматично з гілок `runner-<host>` (`scripts/report/build_status.py`, Action `status.yml`). Не редагувати вручну.

## Зараз

| ПК | стан | останній звіт | прогонів зробив |
|---|---|---|---:|
| StarDestroyer | не рахує | 2026-11-16 09:14 +0200 | 47 |
| Teodor | ⚠ звітів нема понад поріг (ПК вимкнений або воркер завис) | 2026-11-13 18:49 +0200 | 5 |

## Сиквенси × методи

| # | seq | dpvo:mono (×3) | wheel_odom:odom (×1) |
|---:|---|---|---|
| 1 | seq01 | ✅ 3/3 Teodor | ✅ 1/1 StarDestroyer |
| 2 | seq02 | ✅ 3/3 StarDestroyer 1 + Teodor 2 | ✅ 1/1 StarDestroyer |
| 3 | seq03 | ✅ 3/3 StarDestroyer | ✅ 1/1 StarDestroyer |
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
|  | **разом** | **39/39** | **13/13** |

Клітинка: готово/треба і хто рахував (кілька ПК: скільки прогонів кожен). ✅ усе готово, ⏳ зараз рахується, ✗N — N прогонів позначені невдалими (`.bad_run`).
«Звітів нема»: гілка ПК не оновлювалась понад 3 год.

Інші результати на гілках (не входять у таблицю):
- `dpvo:mono tum fr1_xyz`

_Зміст востаннє змінився: 2026-11-16 08:37 UTC_
