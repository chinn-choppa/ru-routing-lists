# Security review: автоматическая публикация routing-data releases

**Статус:** reviewed for implementation; merge разрешать только после включения защиты `main`

**Дата:** 2026-09-08

## Цель и граница

Workflow может автоматически публиковать только policy-neutral artifacts этого repository. Он не имеет доступа к Flexus production, Remnawave, клиентским данным или каким-либо downstream deployment credentials.

Единственная новая write-capability — `contents: write` у отдельного release job, необходимая для создания GitHub Release/tag. Build/test job остаётся read-only.

## Основные риски и controls

### Write token попадает в PR-код

Control:

- top-level `permissions: {}`;
- `build` получает только `contents: read`;
- `release` получает `contents: write` только на уровне job;
- `release` запускается только для `refs/heads/main` и никогда для `pull_request`;
- `pull_request_target` не используется.

### Supply-chain substitution GitHub Actions

Control:

- `actions/checkout`, `actions/setup-python`, `actions/upload-artifact` и `actions/download-artifact` pinned на exact commit SHA;
- checkout использует `persist-credentials: false`;
- release shell использует GitHub-hosted `gh` CLI только внутри write-scoped job.

Residual risk: версия `gh` следует GitHub-hosted runner image, а не pin-ится отдельным binary hash. Для текущего маленького release surface это принимается; переход на сторонний release action только увеличил бы supply-chain surface.

### Mutable upstream меняется неожиданно

Control:

- tracked branch сначала resolves в exact commit SHA;
- source file resolves в exact Git blob SHA;
- release manifest сохраняет immutable revision/blob;
- license file имеет отдельный approved Git blob SHA;
- изменение upstream license blob блокирует автоматический build до нового review;
- builder повторно проверяет fetched source bytes по Git blob SHA.

### Нормальный upstream update превращается в плохой release

Control:

- semantic parser fail-closed;
- empty/invalid data blocked;
- absolute entry-count ranges;
- relative entry-count и byte-size anomaly guards относительно latest published manifest;
- source freshness windows;
- identical artifact SHA-256 не создаёт новый release;
- manifest schema/artifact-set change не публикуется автоматически.

### Race между двумя scheduled/manual runs

Control:

- workflow concurrency сериализует runs для одного ref;
- release job повторно скачивает latest published manifest и повторно выполняет release plan непосредственно перед публикацией.

### Частично загруженный публичный release

Control:

- сначала создаётся **draft** release;
- все assets затем скачиваются обратно;
- проверяется точный набор файлов и `SHA256SUMS`;
- только после успешной проверки draft становится public.

Если job падает до последнего шага, возможен оставшийся draft/tag, но incomplete release не становится published/latest. Его удаление выполняется отдельной ручной операцией после проверки причины.

### Автоматический release случайно меняет production

Control:

- workflow не содержит downstream webhook/deploy step;
- release job пишет только GitHub Release/tag этого repository;
- private Flexus integration обязана отдельно pin exact release/schema/hash и остаётся отдельным approval boundary.

## Обязательная защита `main` перед merge

Write-scoped release workflow делает целостность default branch частью security boundary. Поэтому перед merge release automation `main` должен быть защищён repository ruleset/branch protection минимум так:

- изменения `main` только через pull request;
- required status check для build job этого workflow;
- require conversation resolution;
- block force pushes;
- block branch deletion;
- require linear history, так как проект использует squash merge;
- для single-maintainer repository допустимо `0` required approvals, чтобы не создать self-deadlock; при появлении независимого reviewer — поднять до `1`.

Отдельно рекомендуется tag ruleset для `v*`: запретить update/delete существующих release tags, не запрещая их создание GitHub Actions.

## Итог review

При выполнении защиты `main` residual risk приемлем для публичного data repository: write permission минимальна, не доступна PR jobs, publication fail-closed, а неполная публикация остаётся draft. Никаких production credentials или downstream write permissions добавлять не требуется.
