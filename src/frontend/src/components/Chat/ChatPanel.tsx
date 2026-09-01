/**
 * Conversational chat interface — voice + text input/output, multi-turn
 * history display.
 *
 * Owner: P5 (Frontend Engineer, Core UI).
 * Implements: FR-UI-1, FR-UI-4.
 * Reference: HLD v1.0 §3 "Web Client", LLD v1.0 §5.2 (WebSocket message shapes).
 */
import { useEffect, useState, useRef } from "react";
import { wsClient } from "../../api/wsClient";
import type { ServerMessage, ServerFinalResponse } from "../../api/wsClient";
import "./ChatPanel.css";

type ChatMessage = {
  id: string;
  sender: "user" | "system";
  text: string;
  verdict?: ServerFinalResponse["verdict"];
};

export default function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [sessionId] = useState(() => {
    return localStorage.getItem("orca_session") || `sess_${Math.random().toString(36).substring(2, 9)}`;
  });

  useEffect(() => {
    localStorage.setItem("orca_session", sessionId);

    wsClient.connect(sessionId, (msg: ServerMessage) => {
      if (msg.type === "final_response") {
        setMessages((prev) => [
          ...prev,
          {
            id: Date.now().toString(),
            sender: "system",
            text: msg.text,
            verdict: msg.verdict,
          },
        ]);
      } else if (msg.type === "error") {
        setMessages((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "system", text: `Error: ${msg.message}` },
        ]);
      }
      // TODO(P5, Sprint 3): hand off trace_update to TraceViewer
    });
  }, [sessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSendText = () => {
    if (!inputText.trim()) return;

    setMessages((prev) => [
      ...prev,
      { id: Date.now().toString(), sender: "user", text: inputText },
    ]);

    wsClient.send({ type: "query", mode: "text", text: inputText });
    setInputText("");
  };

  return (
    <section className="chat-container" aria-label="Conversation">
      <div className="chat-history">
        {messages.map((m) => (
          <div key={m.id} className={`chat-message ${m.sender}`}>
            {m.verdict && (
              <div className={`verdict-banner ${m.verdict}`} aria-label={`Verdict: ${m.verdict}`}>
                {m.verdict}
              </div>
            )}
            <div>{m.text}</div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        <input
          type="text"
          className="chat-input"
          placeholder="Ask a question..."
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSendText()}
          aria-label="Message input"
        />
        <button className="btn-icon" onClick={handleSendText} aria-label="Send message">
          ➤
        </button>
      </div>
    </section>
  );
}
