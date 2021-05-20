# Change Log

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](http://keepachangelog.com/)
and this project adheres to [Semantic Versioning](http://semver.org/).

## [Unreleased] - yyyy-mm-dd

## [0.3.2] - 2021-03-03

### Changed

- Reduce load time for tracker list on admin site

## [0.3.1] - 2021-03-03

### Fixed

- Replaces celery_once with cache lock to ensure calls to ZKB RedisQ are atomic, because it looks like celery_once does not fully guarantee atomicity. [#10](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/10)

## [0.3.0] - 2021-02-20

### Update notes

Please re-run **killtracker_load_eve** to get all the newly added types.

### Added

- Add ping groups for trackers [#11](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/11)
- Option to set colors for Discord embed per tracker [#8](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/8)
- Author on Discord embed now shows victim organization [#8](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/8)
- Add Orbital Infrastructure to victim groups [#19](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/19)
- Option to deactivate webhook branding [#18](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/18)
- Added fighters and mining drones (e.g. Excavators) to tracker clauses [#12](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/12)
- Added timeouts to all tasks to prevent pileup during outage

### Changed

- Restructured tasks to improve scalability, performance and resilience [#20](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/20)
- Killtracker will no longer start when ESI is offline
- Remove support for Django 2.1 & 3.0 [#17](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/17)
- Significantly improved task performance with added caching
- Improved handling of potential HTTP 429 errors
- Improved handling of cases when ZKB does not return JSON
- Reduced default for KILLTRACKER_MAX_KILLMAILS_PER_RUN to 200
- Integrated allianceauth-app-utils

### Fixed

- Incompatible with django-redis-cache 3.0
- Fix attempt: Occasionally occurring transaction timeouts [#14](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/14)
- Migrations fail on MySQL 8 due to default for TextField
- Occasional 429 errors from ZKB RedisQ [#22](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/22)

## [0.2.6] - 2020-12-09

### Added

- Now sending proper user agent to ESI and ZKB
- pre-commit for white spaces, EOF and black

### Fixed

- Storing killmails is broken [#15](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/15)

## [0.2.5] - 2020-09-24

### Added

- Add test matrix for Django 3+
- Reformat for new Black version

## [0.2.4] - 2020-08-11

### Changed

- Initial data during installation is now loaded with a new management command: killtracker_load_eve

### Fixed

- Tracker will no longer break on ship types, which are added by CCP after the initial data load from ESI

## [0.2.3] - 2020-08-08

### Added

- Shows list of activated clauses for each tracker in tracker list
- Improved validations prevent the creation of invalid trackers

## [0.2.2] - 2020-08-04

### Changed

- Improved how "main group" is shown on killmails

### Fixed

- Retry of failed sending for killmails not working correctly
- Corp link of final attacker did not work ([#4](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/4))

## [0.2.1] - 2020-07-29

### Added

- Show region name on killmails ([#2](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/2))

### Changed

- Show ISK values in human readable form, we now use same format as zKillboard ([#3](https://gitlab.com/ErikKalkoken/aa-killtracker/-/issues/3))

## [0.2.0] - 2020-07-27

### Added

- Initial public release
