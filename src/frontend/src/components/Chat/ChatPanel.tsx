/**
 * Conversational chat interface — voice + text input/output, multi-turn
 * history display.
 *
 * Owner: P5 (Frontend Engineer, Core UI).
 * Implements: FR-UI-1, FR-UI-4.
 * Reference: HLD v1.0 §3 "Web Client", LLD v1.0 §5.2 (WebSocket message shapes).
 */
import { useState } from "react";
import type { ServerFinalResponse } from "../../api/wsClient";
import "./ChatPanel.css";

type ChatMessage = {
  id: string;
  sender: "user" | "system";
  text: string;
  verdict?: ServerFinalResponse["verdict"];
};

// Hardcoded mock to prove the layout renders a ServerFinalResponse end-to-end.
const MOCK_RESPONSE: ChatMessage = {
  id: "mock-1",
  sender: "system",
  text: "Conditions near Kochi are currently clear. Sea state is calm with wave heights below 1.5 m. PFZ advisory suggests good fishing potential 12 nm southwest.",
  verdict: "SAFE",
};

export default function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([MOCK_RESPONSE]);
  const [inputText, setInputText] = useState("");

  const handleSendText = () => {
    if (!inputText.trim()) return;

    setMessages((prev) => [
      ...prev,
      { id: Date.now().toString(), sender: "user", text: inputText },
    ]);

    // TODO(P5, Sprint 1): replace with real/mock WebSocket response
    setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        { ...MOCK_RESPONSE, id: Date.now().toString() },
      ]);
    }, 500);

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
