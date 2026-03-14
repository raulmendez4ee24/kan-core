# Tu Autonomía — Guía para todos (sin programar)

Esta guía explica **qué hace** tu autonomía y **cómo la usas**, con palabras sencillas. No necesitas saber programar.

---

## ¿Qué es “Tu Autonomía”?

Es un **programa que trabaja en tu computadora por su cuenta**. Tú le dices qué quieres (por ejemplo: “descubre las APIs de esta página” o “abre el navegador y busca documentación”) y él lo hace solo.

- **No se llama “Claw”**: es **tu** autonomía, con tu nombre / tu proyecto.
- **Funciona como un asistente**: hace tareas repetitivas o técnicas por ti.
- **Descubre APIs**: encuentra en internet qué servicios o conexiones ofrece una página o un servicio y los anota.
- **Guarda las claves encriptadas**: si encuentra contraseñas o claves de API, las guarda de forma protegida (encriptadas).
- **Usa “inteligencia” (GPT)**: para decidir qué pasos dar y en qué orden.

---

## ¿Qué hace por ti? (en palabras simples)

1. **Controla tu computadora**
   - Abre el navegador, hace clics, escribe en páginas, como si fuera una persona usando la PC.

2. **Busca y anota APIs**
   - Entra a una web o documentación y detecta qué “APIs” (conexiones que usan otros programas) hay.
   - Las guarda en tu sistema para que después puedas usarlas o revisarlas.

3. **Protege lo que encuentra**
   - Si encuentra claves o contraseñas, las guarda **encriptadas** (codificadas) para que no estén en claro.

4. **Toma decisiones**
   - Con ayuda de GPT, el programa piensa qué hacer paso a paso para cumplir lo que le pediste.

5. **Se ejecuta en la terminal**
   - Todo se hace desde la “ventana negra” (terminal) de tu computadora; no necesitas otra interfaz.

---

## ¿Cómo lo uso?

**Si es solo para ti** (no tienes “id de cliente”): no hace falta poner ningún id. Se usa modo “para mí” y se crea o reusa un cliente personal solo.

1. **Abrir la terminal** (la ventana donde se escriben comandos).
2. **Ir a la carpeta del proyecto** e iniciar “Tu Autonomía”:
   - **Para ti (sin id de cliente):**  
     `python scripts/run_autonomy.py`  
     o:  
     `python scripts/run_autonomy.py --personal`
   - Si ya tienes un id de cliente:  
     `python scripts/run_autonomy.py --client-id "tu-id"`
3. **Escribir lo que quieres** cuando aparezca:
   - `Autonomía>`
   - Por ejemplo:
     - “descubre APIs de https://ejemplo.com”
     - “objetivo: buscar todas las APIs de Stripe”
     - O “help” para ver los comandos.
4. **Salir** escribiendo: `exit`.

No hace falta programar: solo escribir frases o comandos cortos en esa ventana.

---

## Comandos que puedes usar (explicación simple)

- **discover** + una dirección web  
  → Le dices: “entra a esta página y busca qué APIs tiene”.

- **browser** + una dirección web  
  → Abre el navegador en esa página y busca APIs ahí.

- **terminal** + un comando  
  → Ejecuta un comando en la computadora y usa el resultado para buscar APIs (más técnico).

- **goal** + lo que quieres  
  → Le das un objetivo en lenguaje natural (ej.: “descubre todas las APIs de X”) y él intenta hacerlo solo.

- **help**  
  → Muestra la lista de comandos.

- **exit**  
  → Cierra Tu Autonomía.

---

## ¿Qué es una “API” (muy resumido)?

Una **API** es como un “menú” que un servicio en internet ofrece para que otros programas se conecten y pidan datos o acciones (por ejemplo: ver pedidos, enviar mensajes, etc.).  
Tu autonomía **encuentra** esas APIs en páginas o documentación y **las anota y guarda** para que después puedas usarlas o revisarlas de forma ordenada y segura.

---

## ¿Qué es “encriptar”?

**Encriptar** significa guardar una clave o contraseña de forma **codificada**, de modo que si alguien ve el archivo o la base donde está guardada, no pueda leerla sin la “llave” correcta.  
Tu autonomía guarda así las claves que descubre, para que no queden en texto claro.

---

## Resumen en una frase

**Tu Autonomía** es tu propio programa que controla la computadora, descubre APIs, las guarda encriptadas y usa inteligencia (GPT) para decidir los pasos; todo desde la terminal y **sin nombre “Claw”** — es tuyo.

Si quieres, en el mismo proyecto hay documentación más técnica (para desarrolladores); esta guía está pensada para que cualquiera entienda **qué hace** y **cómo se usa**, sin saber programar.
