# ru-routing-lists

Публичный репозиторий воспроизводимых routing datasets для российских доменов и IP-сетей.

Проект отделяет **данные** от продуктовой VPN-политики: здесь находятся только публичные источники, provenance, deterministic builder, проверки и generated artifacts. Конкретный VPN-сервис сам решает, какие категории использовать как direct/proxy/whitelist и когда их выкатывать в production.

## Первый vertical slice

Первая версия строит два нейтральных набора:

- `ru-domain-suffixes.txt` — российские доменные суффиксы/TLD из `v2fly/domain-list-community`;
- `ru-ipv4-cidrs.txt` — агрегированные RU IPv4 prefixes из `ipverse/country-ip-blocks`.

`ru-ipv4-cidrs.txt` отражает **официальную делегацию адресных ресурсов по стране**, а не гарантированное фактическое местоположение маршрута/сервера. Для operational geolocation нужен отдельный dataset и отдельный quality contract.

Первый CIDR source намеренно выбран с простым redistribution contract: `ipverse/country-ip-blocks` публикует country-prefix data под CC0 1.0. GeoLite2-derived datasets пока не ship-ятся, потому что их EULA/retention obligations требуют отдельного моделирования перед immutable historical releases.

## Source tracking и воспроизводимость

`sources.json` содержит review-approved source contract: repository/ref/path, license metadata и approved license Git blob SHA. Scheduled build сначала resolves mutable upstream ref в:

- exact commit SHA;
- exact source Git blob SHA;
- exact license Git blob SHA;
- immutable raw URL.

Если upstream license blob изменился, автоматический release блокируется до нового review. Builder затем повторно проверяет fetched bytes по Git blob SHA.

Сам release `manifest.json` хранит exact revisions/blobs/source URLs, поэтому опубликованный artifact остаётся воспроизводимым даже при дальнейших изменениях upstream branch.

## Лицензирование данных

`LICENSE` относится к собственному коду и документации этого репозитория. Generated datasets сохраняют условия upstream-лицензий; см. `DATA_LICENSES.md` и machine-readable `sources.json`.

Источник с `redistribution.allowed != true` не может попасть в публикуемый artifact.

## Локальная сборка

Сборка из уже pinned `sources.json`:

```bash
python src/build.py --sources sources.json --output dist
python -m unittest discover -s tests -v
```

Проверка актуальных tracked revisions перед candidate build:

```bash
python -m src.refresh_sources --sources sources.json --output resolved-sources.json
python src/build.py --sources resolved-sources.json --output dist
```

`manifest.json` намеренно не содержит wall-clock build timestamp: одинаковые resolved inputs дают байт-в-байт одинаковый output. Время конкретного CI run/release остаётся metadata GitHub Actions/GitHub Release.

## GitHub Actions и Releases

Workflow выполняется на pull request, `main`, вручную и по расписанию каждые 6 часов.

Build job имеет только `contents: read` и выполняет:

1. tests;
2. upstream resolution + license-blob gate;
3. deterministic build;
4. checksum verification;
5. comparison с latest published `manifest.json`;
6. freshness/size/change guards из `release-policy.json`.

Если artifact SHA-256 не изменились, новый release не создаётся.

Отдельный release job получает `contents: write` **только на `main` и не на pull request**. Он повторно проверяет latest release, создаёт draft, скачивает assets обратно, проверяет точный набор файлов и `SHA256SUMS`, и только после этого публикует release.

Security review этого write boundary: `docs/release-security-review.md`.

## Безопасность и границы

В репозиторий запрещено добавлять secrets, private infrastructure, клиентские UUID/subscription paths, private VPN endpoints и любые другие данные конкретного оператора. Здесь также нет automatic production rollout в сторонние системы.

Default branch должна быть защищена от direct/force pushes перед включением write-scoped release automation; рекомендуемая конфигурация описана в security review.

## WHITELIST candidate categories (не операторский PASS)

Дополнительно собираются **две отдельные категории-кандидата** из `vahellame/russia-whitelist-geosite`:

- `whitelist-candidate-gov.txt` — `data/category-gov`, 16 исходных правил;
- `whitelist-candidate-finance.txt` — `data/category-finance`, 34 исходных правила.

Это **не** список гарантированно доступных доменов конкретной сети или оператора. В `manifest.json` эти sources имеют `verification.operator_access=unverified`. Результаты операторских измерений сюда не подмешиваются. Не использовать эти файлы как автоматические `direct` исключения в стандартном Auto.

Формат — текстовые Xray domain rules: `full:example.ru` означает только точное имя, `domain:example.ru` — имя и поддомены. Builder удаляет дубликаты, нормализует IDNA, игнорирует upstream `@provider` annotations, отклоняет `include:`/`regexp:` и некорректный синтаксис. Пересечение между разными публикуемыми категориями останавливает сборку. Никакого расширения `full:` до suffix не происходит.

Лицензия исходных curated categories — MIT (copyright 2026 vahellame), с сохранением attribution и exact license Git blob; подробности в `DATA_LICENSES.md`. Списки ROSCOMVPN используются только как oracle при сравнении: его общий `whitelist` включает сторонние источники с неподтверждёнными правами на дальнейшее переиздание. Vahellame GeoIP `trash` и `category-public-dns` намеренно исключены: они не являются адресами доступности для VPN.

Переход с двух старых файлов на четыре является **явно разрешённым расширением** в `release-policy.json.allowed_new_artifacts`; произвольное добавление или удаление артефактов блокируется. Release не активирует никакую клиентскую или production routing policy. Для Flexus используется только ручной exact pin опубликованной версии с SHA-256, shadow-only comparison и отдельное подтверждение перед canary. Откат — возврат к прежнему pin.
