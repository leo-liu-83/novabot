# Versioning and releases

How versions are numbered, what a release contains, and how the changelog works. Branch and push
mechanics live in `AGENTS.md` at the repository root (development branch only, not shipped with
releases).

## 1. Version numbers

[Semantic Versioning 2.0.0](https://semver.org/): `MAJOR.MINOR.PATCH`.

While the project is `0.x`:

| Change | Action | Example |
|---|---|---|
| New feature or behaviour change | MINOR +1, PATCH back to 0 | Projects → `0.2.0` |
| Bug fixes, documentation, dependency updates only | PATCH +1 | Documentation pass → `0.1.1` |
| Breaking change (config shape, HTTP API) | still MINOR in `0.x`, flagged as "Breaking" in the changelog | |
| A batch with both features and fixes | treat as MINOR | |

`0.x` makes no promise that the config format or HTTP API is stable. `1.0.0` is reserved for the
point where both are frozen; do not jump ahead just to look stable.

Pre-releases carry **no** `-rc1`-style suffix: a pre-release is a branch (the development `main`),
the same version can be pre-released several times, and each time only the content is synced.

## 2. Single source of truth

`APP_VERSION` in `config/base_config.py` is the only source. Everything else either reads it or is
guarded by a test:

| Location | Handling |
|---|---|
| Version line at the top of `README.md` / `README.en.md` | Written by hand, asserted to match `APP_VERSION` |
| `version` in `web/js/mock.js` | Written by hand, asserted to match |
| Install examples' directory name | No version in the path (`cd /path/to/novabot`) |
| Web UI | Reads the version from `/system` at runtime |

Bump `APP_VERSION`, run the tests, and they will point at anything that fell behind.

## 3. When to bump

- On the development branch `master`, as one commit: `chore(release): 0.2.0`
- Push it together with the rest of the work to `origin master`
- **Bump by batch, not by commit**: feature commits only ship code and leave the version alone;
  bump once when a batch is ready to be delivered, and let the maintainer pick the number.
  Do not bump separately for every feature
- Pre-release (`scripts/prerelease.sh`) syncs content to `origin main` and never touches the version
- Publish (`scripts/publish.sh`) only reads the version; it never decides it

## 4. Release artifacts

Each version on GitHub consists of:

- One commit: `release: <version>`, authored and committed with the noreply identity
- One annotated tag: `v<version>`

Tag and commit correspond one-to-one. **A version cannot be released twice**: if the content
changes, the version must be bumped first, and the publish script refuses when a tag already points
at a different commit. That keeps the GitHub history readable against the version.

When release notes are wanted, create a GitHub Release from the tag and use the matching section of
`CHANGELOG.md` as its body — never paste development commit messages, which would leak the
development record into the public repository.

## 5. Changelog

`CHANGELOG.md` records three groups — **Added / Changed / Fixed** — plus **Breaking** when a
breaking change lands.

- Updated before publishing and shipped with the release
- Only user-visible changes; internal refactors stay out
- The publish script refuses to run when the current version has no section

## 6. Release checklist

1. Bump `APP_VERSION` on `master` and add the changelog section
2. Full test suite passes (`pytest`)
3. Say "推送预发布" → content is synced to `origin main`
4. Say "发布" → a `release: <version>` commit and a `v<version>` tag are pushed to GitHub
5. Create a GitHub Release from the tag when notes are needed
