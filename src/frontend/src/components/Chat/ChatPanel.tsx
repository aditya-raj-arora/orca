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
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [traceSteps, setTraceSteps] = useState<string[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const [sessionId] = useState(() => {
    return localStorage.getItem("orca_session") || `sess_${Math.random().toString(36).substring(2, 9)}`;
  });

  useEffect(() => {
    localStorage.setItem("orca_session", sessionId);

    wsClient.connect(sessionId, (msg: ServerMessage) => {
      if (msg.type === "final_response") {
        setTraceSteps([]);
        setIsProcessing(false);
        setMessages((prev) => [
          ...prev,
          {
            id: Date.now().toString(),
            sender: "system",
            text: msg.text,
            verdict: msg.verdict,
          },
        ]);
        if (msg.audio_base64) {
          const audio = new Audio(`data:audio/wav;base64,${msg.audio_base64}`);
          audio.play().catch(e => console.error("Audio playback failed", e));
        }
      } else if (msg.type === "error") {
        setTraceSteps([]);
        setIsProcessing(false);
        setMessages((prev) => [
          ...prev,
          { id: Date.now().toString(), sender: "system", text: `Error: ${msg.message}` },
        ]);
      } else if (msg.type === "trace_update") {
        setTraceSteps((prev) => [...prev, msg.step]);
        window.dispatchEvent(new CustomEvent("trace_update", { detail: msg }));
      }
    });
  }, [sessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSendText = () => {
    if (!inputText.trim() || isProcessing) return;

    setMessages((prev) => [
      ...prev,
      { id: Date.now().toString(), sender: "user", text: inputText },
    ]);

    setIsProcessing(true);
    wsClient.send({ type: "query", mode: "text", text: inputText });
    setInputText("");
  };

  const handleStopProcess = () => {
    setIsProcessing(false);
    // Future: send abort signal to backend if supported
  };

  const toggleRecording = async () => {
    if (isRecording) {
      mediaRecorderRef.current?.stop();
      setIsRecording(false);
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mediaRecorder = new MediaRecorder(stream);
        mediaRecorderRef.current = mediaRecorder;
        audioChunksRef.current = [];

        mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0) audioChunksRef.current.push(e.data);
        };

        mediaRecorder.onstop = () => {
          const audioBlob = new Blob(audioChunksRef.current, { type: "audio/webm" });
          const reader = new FileReader();
          reader.readAsDataURL(audioBlob);
          reader.onloadend = () => {
            const base64data = reader.result?.toString().split(',')[1];
            if (base64data) {
              setMessages((prev) => [
                ...prev,
                { id: Date.now().toString(), sender: "user", text: "\uD83C\uDFA4 (Voice Query)" },
              ]);
              setIsProcessing(true);
              wsClient.send({ type: "query", mode: "voice", audio_base64: base64data });
            }
          };
          stream.getTracks().forEach(track => track.stop());
        };

        mediaRecorder.start();
        setIsRecording(true);
      } catch (err) {
        console.error("Microphone access denied", err);
      }
    }
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
        {traceSteps.length > 0 && (
          <div className="chat-message system trace-progress" aria-live="polite" aria-label="Agent progress">
            <div className="trace-label">Processing…</div>
            {traceSteps.map((step, i) => (
              <div key={i} className="trace-step">{step}</div>
            ))}
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        <button
          className={`btn-icon ${isRecording ? "recording" : ""}`}
          onClick={toggleRecording}
          disabled={isProcessing}
          aria-label={isRecording ? "Stop recording" : "Start recording"}
        >
          {isRecording ? (
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12"></rect>
            </svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"></path>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
              <line x1="12" y1="19" x2="12" y2="22"></line>
            </svg>
          )}
        </button>
        <input
          type="text"
          className="chat-input"
          placeholder="Ask a question..."
          value={inputText}
          disabled={isProcessing}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSendText()}
          aria-label="Message input"
        />
        <button 
          className={`btn-icon ${isProcessing ? "stop-btn" : ""}`} 
          onClick={isProcessing ? handleStopProcess : handleSendText} 
          aria-label={isProcessing ? "Stop processing" : "Send message"}
        >
          {isProcessing ? (
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12"></rect>
            </svg>
          ) : (
            "➤"
          )}
        </button>
      </div>
    </section>
  );
}
