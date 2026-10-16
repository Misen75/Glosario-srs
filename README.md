# Glosario Técnico Inmutable + Entrenador SRS (100% Cloud Serverless 24/7)

Sistema de arquitectura limpia para el estudio y retención a largo plazo de conceptos clave de Diseño de Software y Arquitectura. Diseñado para vivir **100% en la nube de forma gratuita** (Render.com + Supabase PostgreSQL) sin depender de que tu laptop esté encendida.

---

## 🏛️ Arquitectura Cloud-Native

```
glosario-srs/
├── data/
│   └── DS-GlosarioTerminos.html # Archivo fuente original (INTACTO e inalterado)
├── scripts/
│   └── seed_from_html.py        # Migración directa a PostgreSQL en la nube (DATABASE_URL)
├── src/
│   ├── domain/
│   │   ├── models.py            # Entidades inmutables (Area, Category, Term, SRSState, ReviewLog)
│   │   └── srs_scheduler.py     # Algoritmo SM-2 puro (1=Olvidado, 2=Dudé, 3=Dominado)
│   ├── infrastructure/
│   │   ├── db_repository.py     # PostgreSQL Repository con Triggers de inmutabilidad (Supabase)
│   │   └── ai_enricher.py       # Conector Gemini API con JSON estructurado y validación
│   ├── application/
│   │   └── use_cases.py         # Casos de uso compartidos entre PWA, Webhook y Cron
│   ├── interfaces/
│   │   ├── api_server.py        # Servidor FastAPI + PWA móvil
│   │   ├── webhook_routes.py    # POST /api/telegram/webhook (Serverless Telegram Bot)
│   │   └── cron_routes.py       # POST /api/cron/trigger-srs (Recordatorios a las 08:30 y 20:00)
│   ├── web/                     # Frontend PWA móvil/desktop (Guía Zoom, Compendio A4, Flashcards)
│   │   ├── index.html
│   │   ├── styles.css
│   │   ├── app.js
│   │   ├── manifest.webmanifest
│   │   ├── icon.svg
│   │   └── sw.js
│   └── bootstrap.py             # Composition root unificado (lee DATABASE_URL)
├── tests/
│   └── test_all.py              # Suite de pruebas automatizadas (SM-2, Webhook, Cron, Security)
├── render.yaml                  # Blueprint para despliegue automático en Render.com
├── requirements.txt             # Dependencias ligeras serverless (psycopg2-binary, fastapi, httpx...)
└── .env.example
```

---

## 🚀 Despliegue Paso a Paso en la Nube (100% Gratis)

### Paso 1: Base de Datos en Supabase (Gratis)
1. Crea un proyecto en [Supabase](https://supabase.com/).
2. Ve a **Project Settings** > **Database** > **Connection string** (modo URI).
3. Copia tu `DATABASE_URL` con formato:
   ```
   postgresql://postgres:[TU_PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres
   ```

### Paso 2: Ejecutar la Migración Inicial a Supabase
Desde tu máquina (solo por única vez):
```bash
# Configura tu DATABASE_URL en .env o pásala por parámetro:
python scripts/seed_from_html.py --db "postgresql://postgres:tu_pass@db.xxxx.supabase.co:5432/postgres"
```
*Esto creará las 3 tablas con triggers de inmutabilidad en PostgreSQL e insertará los términos y categorías de `DS-GlosarioTerminos.html`.*

---

### Paso 3: Desplegar en Render.com (Gratis)
1. Sube este repositorio a tu GitHub o GitLab.
2. En [Render.com](https://render.com), haz clic en **New +** > **Blueprint**.
3. Selecciona tu repositorio (Render detectará automáticamente `render.yaml`).
4. Rellena las variables de entorno en el panel:
   - `DATABASE_URL`: Tu URI de Supabase del Paso 1.
   - `GEMINI_API_KEY`: Tu API Key de Google AI Studio.
   - `TELEGRAM_BOT_TOKEN`: Token provisto por [@BotFather](https://t.me/BotFather).
   - `TELEGRAM_CHAT_ID`: Tu Chat ID personal (ej. `123456789`).
   - `CRON_SECRET`: Render generará uno automáticamente, o puedes definir uno propio.
5. Haz clic en **Apply**. ¡Tu aplicación estará online en `https://tu-app.onrender.com`!

---

### Paso 4: Activar el Webhook de Telegram
Configura a Telegram para que envíe los mensajes a tu servidor en Render en lugar de usar polling:
```bash
curl -F "url=https://tu-app.onrender.com/api/telegram/webhook" https://api.telegram.org/bot<TU_TELEGRAM_BOT_TOKEN>/setWebhook
```
*(Si configuraste `TELEGRAM_WEBHOOK_SECRET`, agrega `-F "secret_token=tu_secreto"`).*

---

### Paso 5: Configurar los Recordatorios SRS (08:30 y 20:00)
Crea 2 tareas gratuitas en [cron-job.org](https://cron-job.org/) o EasyCron:
- **URL:** `https://tu-app.onrender.com/api/cron/trigger-srs`
- **Método:** `POST`
- **Headers:** `CRON_SECRET: tu_secreto`
- **Horarios:**
  - Mañana: `30 8 * * *` (08:30)
  - Noche: `0 20 * * *` (20:00)

*Cuando el cron dispare la petición, el backend consultará los términos con `next_review_date <= CURRENT_DATE` en PostgreSQL y enviará automáticamente la primera tarjeta interactiva con botones a tu Telegram.*

---

## 🧪 Pruebas Automatizadas
Para verificar el comportamiento del algoritmo SM-2 y la seguridad de las rutas:
```bash
python -m unittest tests/test_all.py
```
