# ru-routing-lists

Публичный репозиторий воспроизводимых routing datasets для российских доменов и IP-сетей.

Проект отделяет **данные** от продуктовой VPN-политики: здесь находятся только публичные источники, provenance, deterministic builder, проверки и generated artifacts. Конкретный VPN-сервис сам решает, какие категории использовать как direct/proxy/whitelist и когда их выкатывать в production.

## Первый vertical slice

Первая версия строит два нейтральных набора из immutable upstream revisions:

- `ru-domain-suffixes.txt` — российские доменные суффиксы/TLD из `v2fly/domain-list-community`;
- `ru-ipv4-cidrs.txt` — агрегированные RU IPv4 prefixes из `ipverse/country-ip-blocks`.

Оба upstream pin-ятся на точные commit SHA и Git blob SHA. Builder проверяет fetched bytes, нормализует, дедуплицирует и детерминированно сортирует данные, затем создаёт `manifest.json` и `SHA256SUMS`.

Первый CIDR source намеренно выбран с простым redistribution contract: `ipverse/country-ip-blocks` публикует country-prefix data под CC0 1.0. GeoLite2-derived datasets пока не ship-ятся, потому что их EULA/retention obligations требуют отдельного моделирования перед immutable historical releases.

## Лицензирование данных

`LICENSE` относится к собственному коду и документации этого репозитория. Generated datasets сохраняют условия upstream-лицензий; см. `DATA_LICENSES.md` и machine-readable `sources.json`.

Источник с `redistribution.allowed != true` не может попасть в публикуемый artifact.

## Сборка

```bash
python src/build.py --sources sources.json --output dist
python -m unittest discover -s tests -v
```

Network access нужен только на fetch-stage. Тесты используют локальные fixtures и не зависят от сети.

`manifest.json` намеренно не содержит wall-clock build timestamp: одинаковые pinned inputs должны давать байт-в-байт одинаковый output. Время конкретного запуска/релиза остаётся metadata CI/GitHub Release, а freshness входов хранится как immutable upstream revision + source timestamp.

## GitHub Actions

Workflow выполняется на pull request, `main`, вручную и по расписанию. Он имеет только `contents: read`, запускает unit/reproducibility tests, строит `dist/` и загружает его как CI artifact.

Автоматическая публикация GitHub Release пока намеренно не включена: это write-capable workflow и требует отдельного security review перед выдачей `contents: write`.

## Безопасность и границы

В репозиторий запрещено добавлять secrets, private infrastructure, клиентские UUID/subscription paths, private VPN endpoints и любые другие данные конкретного оператора. Здесь также не должно быть автоматического production rollout в сторонние системы.
