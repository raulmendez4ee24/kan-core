export default function LeadForm() {
  return (
    <section id="lead-form" className="pb-20 pt-8">
      <div className="container-landing">
        <div className="rounded-2xl border border-black/10 bg-white/80 p-8 shadow-soft">
          <h2 className="section-title font-semibold">Agenda una demo ejecutiva</h2>
          <p className="section-sub mt-3">En 30 minutos revisamos tu operación y proponemos un plan claro.</p>
          <form className="mt-6 grid gap-3 md:grid-cols-2">
            <input required placeholder="Nombre" className="rounded-xl border border-black/10 bg-white p-3" />
            <input required placeholder="Negocio" className="rounded-xl border border-black/10 bg-white p-3" />
            <input required placeholder="Canal principal" className="rounded-xl border border-black/10 bg-white p-3" />
            <input required type="email" placeholder="Correo" className="rounded-xl border border-black/10 bg-white p-3" />
            <textarea required placeholder="Mensaje" className="md:col-span-2 rounded-xl border border-black/10 bg-white p-3" rows={4} />
            <div className="md:col-span-2 flex flex-wrap gap-3">
              <button type="submit" className="rounded-full bg-ink px-6 py-3 text-sm font-medium text-ivory">Agendar demo</button>
              <a
                href="https://wa.me/5210000000000"
                target="_blank"
                rel="noreferrer"
                className="rounded-full border border-accent/30 px-6 py-3 text-sm font-medium text-accent"
              >
                WhatsApp
              </a>
              <a href="#" className="rounded-full border border-black/15 px-6 py-3 text-sm font-medium text-ink">Calendly / Google Calendar</a>
            </div>
          </form>
          <p className="mt-4 text-xs text-slate-500">Al enviar aceptas el aviso de privacidad.</p>
        </div>
      </div>
    </section>
  );
}
