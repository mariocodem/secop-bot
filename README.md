# Bot de licitaciones SECOP II

Aplicación de escritorio (PySide6) para buscar licitaciones de SECOP II con filtros, guardar perfiles,
revisar automáticamente cada cierto tiempo y recibir por correo los procesos nuevos.

- **Datos**: API oficial de datos abiertos [datos.gov.co – SECOP II Procesos de Contratación](https://www.datos.gov.co/d/p6dx-8zbt)
  (sin captcha, actualizada a diario).
- **Navegador asistido**: abre el proceso en `community.secop.gov.co` con Playwright. Si SECOP pide captcha,
  **lo resuelves tú** en la ventana y el bot continúa (por ejemplo, descargando los documentos a `~/Descargas/secop/<id>/`).
  El bot no evade el captcha.

## Instalación

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
.venv/bin/python main.py
```

## Uso

1. **Buscar**: escribe palabras clave (separadas por coma) y agrega los filtros que quieras con **+ Agregar filtro**
   (departamento, modalidad, precio base, fecha, UNSPSC, entidad…). Doble clic en un resultado lo abre en SECOP;
   con el clic derecho tienes más opciones.
2. **Guardar como perfil…**: guarda la búsqueda actual. En la pestaña **Perfiles** marca la casilla de los que se deben revisar solos.
3. **Automático**: el monitor arranca solo al abrir la app (se puede desactivar en Ajustes). La primera revisión de
   cada perfil solo registra lo que ya existe; desde la segunda se notifican los procesos nuevos (bandeja y correo).
4. **Ajustes y correo**: configura el SMTP (en Gmail usa una *contraseña de aplicación*) y usa **Enviar correo de prueba**.
   La contraseña se guarda en el llavero del sistema si está disponible.

## Enfoque: convocatorias vigentes de tecnología

- **Vigente** = estado *Publicado* y **fecha de cierre de ofertas** (`fecha_de_recepcion_de`) de hoy en adelante.
  Las modalidades sin ofertas (contratación directa, régimen especial) no tienen esa fecha y quedan fuera:
  no son convocatorias a las que se pueda presentar oferta.
- **Tecnología**: el 97 % de los procesos no trae código UNSPSC, así que `secop_bot/tecnologia.py` puntúa
  nombre + descripción con términos ponderados (software, licenciamiento, equipos de cómputo, redes, nube,
  ciberseguridad, conectividad…) y resta en falsos positivos típicos (obra civil, servidores públicos, uniformes…).
  Se acepta con puntaje ≥ 3. Pasa el cursor sobre la columna *Tec* para ver qué términos coincidieron.
- Al abrir la app se crea y activa el perfil **«Tecnología – vigentes»** y arranca el monitor.

## Enterarse primero

- datos.gov.co recarga el dataset de SECOP II **una vez al día** (los procesos aparecen con ~1 día de rezago
  frente a SECOP). El monitor consulta cada 15 min (configurable) la marca de actualización del dataset
  y, apenas cambia, revisa los perfiles y avisa (bandeja del sistema + correo).
- Las alertas se ordenan por prioridad: **⚡ mínima cuantía** primero (plazos de oferta muy cortos y, agotados
  los criterios legales de desempate, puede contar el orden de llegada de la oferta), luego lo que cierra antes.
- **📡 Señal temprana (RFI)**: las *Solicitudes de información a proveedores* son estudios de mercado previos
  al proceso; responderlas te pone en el radar de la entidad antes de que se publique la convocatoria.

Configuración y base de datos: `~/.config/secop-bot/`.

## Tiempo real: correos de SECOP II

En tu cuenta de proveedor SECOP II ve a *Configuración de la cuenta → Suscripción a notificaciones* y crea
**áreas de interés** con tus códigos UNSPSC (tecnología: 4321, 4322, 4323, 8111, 8112, 8116…). SECOP envía un
correo cuando se crea un proceso que coincide. En *Ajustes y correo → Tiempo real* activa **Vigilar el buzón**:
el bot revisa el buzón por IMAP cada 60 s (sin marcar como leído), extrae los procesos y te avisa al instante.
La primera revisión solo marca el punto de partida.

## Ofertas

Pestaña **Ofertas**:

- **Cuenta**: usuario y contraseña de proveedor (la contraseña va al llavero del sistema). Si la dejas vacía,
  inicias sesión tú en la ventana del navegador; la sesión queda guardada.
- **Documentos**: los habilitantes que van en todas las ofertas (RUT, cámara de comercio, RUP, certificaciones…)
  van en la *biblioteca* (`~/.config/secop-bot/documentos/`). Los propios de cada proceso (propuesta técnica,
  oferta económica) van en `~/SECOP/ofertas/<id del proceso>/`; si un archivo se llama igual, manda el del proceso.
- **Modos**:
  - *Simulación* (por defecto): inicia sesión, abre «Presentar oferta», llena el valor, anexa los documentos
    y **se detiene** antes de presentar.
  - *Confirmación*: igual, pero te muestra un resumen y presenta solo si aceptas.
  - *Automático*: presenta la oferta y verifica el mensaje de confirmación de SECOP.
- **Reglas automáticas**: cuando el monitor detecta una convocatoria nueva que cumple *todas* las reglas
  (modalidad, precio base máximo, puntaje de tecnología, carpeta con documentos propios, máximo de ofertas por día),
  crea la oferta con el % del precio base configurado y la ejecuta en el modo elegido.
  Nunca se envían dos ofertas al mismo proceso.
- Desde **Buscar** (botón o clic derecho → *Preparar oferta…*) puedes poner cualquier proceso en la cola.

Se detiene y deja la oferta abierta para que la termines tú cuando: SECOP pide captcha (lo resuelves en la ventana),
el cuestionario tiene **varios campos de precio** (ítems o lotes), la oferta requiere **firma digital** o no aparece
la confirmación de SECOP. Cada paso deja una captura en `~/.config/secop-bot/evidencias/<proceso>/`.

**Calibración**: el flujo se probó contra una réplica local de SECOP, no contra el portal real (requiere cuenta de
proveedor). Antes de usar el modo automático, ejecuta ofertas en *Simulación* y revisa las capturas. Si algún botón
no se encuentra, ajusta `~/.config/secop-bot/selectores.json` (botón *Editar selectores*): cada clave admite
varios selectores de Playwright que se prueban en orden.

> Una oferta presentada en SECOP II es vinculante. Revisa siempre los pliegos: el bot no verifica requisitos
> habilitantes, experiencia ni condiciones técnicas.
