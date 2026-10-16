# perekup-webapp

Хостинг собранной статики Mini App (GitHub Pages). Сюда попадает результат
`npm run build` из проекта `PerekupBot/webapp`.

Важно:

- Пользователи открывают Mini App **не отсюда**, а с адреса воркера:
  https://neon-reseller.neon-reseller-worker.workers.dev
  (воркер проксирует эту статику и обслуживает API с одного адреса —
  поэтому нет проблем с CORS и подписью initData).
- Cron-уведомления об окончании аренды работают внутри самого воркера
  (Cloudflare Cron Triggers, каждые 5 минут). GitHub Actions для этого
  больше не используется.
