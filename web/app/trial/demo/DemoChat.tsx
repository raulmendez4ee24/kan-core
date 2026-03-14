"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type ChatItem = { role: "user" | "assistant"; content: string };

export function DemoChat() {
  const [items, setItems] = React.useState<ChatItem[]>([
    { role: "assistant", content: "Hola. Soy tu asistente de clinica. Que te gustaria agendar?" }
  ]);
  const [text, setText] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function send() {
    const msg = text.trim();
    if (!msg || loading) return;
    setError(null);
    setLoading(true);
    setText("");
    setItems((prev) => [...prev, { role: "user", content: msg }]);

    try {
      const res = await fetch("/api/trial/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, session_id: "trial-demo" })
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(String(body?.detail || body?.error || "chat_failed"));
      }
      const content = String(body?.content || body?.message || body?.text || "").trim();
      setItems((prev) => [
        ...prev,
        { role: "assistant", content: content || JSON.stringify(body) }
      ]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card className="p-0">
      <div className="border-b border-black/10 px-6 py-4">
        <div className="text-sm font-medium">Webchat demo</div>
        <div className="mt-1 text-xs text-black/55">
          Usando <code>/chat/message</code> con credenciales del trial.
        </div>
      </div>

      <div className="max-h-[420px] space-y-3 overflow-auto px-6 py-5">
        {items.map((it, idx) => (
          <div key={idx} className={it.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={
                "max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ring-1 ring-black/10 " +
                (it.role === "user" ? "bg-ink-900 text-white" : "bg-white/70 text-ink-900")
              }
            >
              {it.content}
            </div>
          </div>
        ))}
      </div>

      {error ? (
        <div className="px-6 pb-3 text-sm text-red-700">{error}</div>
      ) : null}

      <div className="flex gap-3 border-t border-black/10 px-6 py-4">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Escribe aqui..."
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <Button type="button" onClick={send} disabled={loading}>
          {loading ? "..." : "Enviar"}
        </Button>
      </div>
    </Card>
  );
}

