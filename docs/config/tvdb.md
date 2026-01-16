---
hide:
  - toc
---
# TVDb

The `tvdb` section of the configuration file is used to configure the connection to TheTVDb.

## Attribute Description

| Attribute | Description | Required | Default |
| :--- | :--- | :---: | :---: |
| `apikey` | Your TVDb API Key. | :fontawesome-solid-circle-check:{ .green } | N/A |
| `pin` | Your TVDb PIN. | :fontawesome-solid-circle-xmark:{ .red } | N/A |
| `cache_expiration` | The number of days to cache the TVDb data for. | :fontawesome-solid-circle-xmark:{ .red } | `60` |
| `language` | The language to use for the TVDb data. | :fontawesome-solid-circle-xmark:{ .red } | `en` |

### Example

```yaml
tvdb:
  apikey: ####################
  pin: ####################
  cache_expiration: 60
  language: en
```