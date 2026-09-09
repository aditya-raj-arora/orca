import { useEffect, useState, useRef } from "react";
import { wsClient } from "../../api/wsClient";
import { createSession, checkSession } from "../../api/session";
import { MAP_UPDATE_EVENT } from "../../api/mapPayload";
import type { ConnectionStatus, ServerMessage, ServerFinalResponse } from "../../api/wsClient";
import "./ChatPanel.css";

type ChatMessage = {
  id: string;
  sender: "user" | "system";
  text: string;
  verdict?: ServerFinalResponse["verdict"];
  // Snapshot of the agent trace that produced this answer (or the partial
  // trace up to a failure) — kept, not discarded, so it's still inspectable
  // after the fact. Collapsed by default; see the <details> below.
  trace?: string[];
};

export default function ChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [traceSteps, setTraceSteps] = useState<string[]>([]);
  // #170: reflects the WebSocket's real state so the UI never just sits
  // there — "closed" means we're mid-reconnect (the backend closes the
  // socket after every query), during which sends are queued, not dropped.
  const [wsStatus, setWsStatus] = useState<ConnectionStatus>("connecting");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const chatHistoryRef = useRef<HTMLDivElement>(null);
  // Mirrors traceSteps so the WS message handler can read the trace-so-far
  // synchronously when a query finishes, without nesting a setState call
  // inside setTraceSteps's updater (which React may invoke more than once).
  const traceStepsRef = useRef<string[]>([]);

  // null until the session is established — either restored from a previous
  // visit or minted by the backend (#104). Every effect that talks to the
  // backend waits for it, so nothing races the POST /api/v1/session.
  const [sessionId, setSessionId] = useState<string | null>(() =>
    localStorage.getItem("orca_session"),
  );
  const [verifyStatus, setVerifyStatus] = useState<"pending" | "failed" | "success">("pending");

  // Establish the session on mount (LLD §5.1). A stored id is verified against
  // the backend first; an id the backend doesn't recognise is replaced rather
  // than reused, which is also what migrates pre-#104 `sess_*` ids.
  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      if (sessionId === null) {
        const created = await createSession();
        if (cancelled) return;
        localStorage.setItem("orca_session", created);
        setSessionId(created);
        setVerifyStatus("success");
        return;
      }

      const status = await checkSession(sessionId);
      if (cancelled) return;

      if (status === "unknown-session") {
        // Stale id: the backend restarted (its session store is in-process),
        // or this is a client-minted id from before #104. Either way there is
        // no server-side context behind it, so start a real one — and drop the
        // local transcript, which would otherwise imply a continuity the
        // backend can no longer honour for FR-PLAN-5 follow-ups.
        localStorage.removeItem(`orca_chat_history_${sessionId}`);
        setMessages([]);
        const created = await createSession();
        if (cancelled) return;
        localStorage.setItem("orca_session", created);
        setSessionId(created);
        setVerifyStatus("success");
        return;
      }

      if (status === "ok") {
        const saved = localStorage.getItem(`orca_chat_history_${sessionId}`);
        if (saved) {
          const parsedSaved = JSON.parse(saved) as ChatMessage[];
          setMessages((prev) => {
            const prevIds = new Set(prev.map((m) => m.id));
            const newSaved = parsedSaved.filter((m) => !prevIds.has(m.id));
            return [...newSaved, ...prev];
          });
        }
        setVerifyStatus("success");
        return;
      }

      // "unreachable": we learned nothing about the session, so keep the id
      // and the local transcript. verifyStatus stays "failed", which stops us
      // overwriting stored history with state we can't vouch for.
      setVerifyStatus("failed");
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Persist messages whenever they change
  useEffect(() => {
    if (sessionId !== null && verifyStatus === "success") {
      localStorage.setItem(`orca_chat_history_${sessionId}`, JSON.stringify(messages));
    }
  }, [messages, sessionId, verifyStatus]);

  const handleNewChat = async () => {
    wsClient.close();
    setMessages([]);
    setTraceSteps([]);
    traceStepsRef.current = [];
    setVerifyStatus("pending");
    const newId = await createSession();
    localStorage.setItem("orca_session", newId);
    setSessionId(newId);
    setVerifyStatus("success");
  };

  useEffect(() => {
    if (sessionId === null) return;  // still bootstrapping — see the effect above
    localStorage.setItem("orca_session", sessionId);

    wsClient.connect(
      sessionId,
      (msg: ServerMessage) => {
        if (msg.type === "final_response") {
          setIsProcessing(false);
          // Hand map_payload to MapPanel (via App) using the same window-event
          // pattern as the trace_update dispatch below. FR-UI-2 / FR-GEO-3, #35.
          window.dispatchEvent(new CustomEvent(MAP_UPDATE_EVENT, { detail: msg.map_payload }));
          const finishedTrace = traceStepsRef.current;
          traceStepsRef.current = [];
          setTraceSteps([]);
          setMessages((prev) => [
            ...prev,
            {
              id: Date.now().toString(),
              sender: "system",
              text: msg.text,
              verdict: msg.verdict,
              trace: finishedTrace,
            },
          ]);
          if (msg.audio_base64) {
            const audio = new Audio(`data:audio/wav;base64,${msg.audio_base64}`);
            audio.play().catch(e => console.error("Audio playback failed", e));
          }
        } else if (msg.type === "error") {
          setIsProcessing(false);
          const finishedTrace = traceStepsRef.current;
          traceStepsRef.current = [];
          setTraceSteps([]);
          setMessages((prev) => [
            ...prev,
            {
              id: Date.now().toString(),
              sender: "system",
              text: `Sorry, I ran into an error: ${msg.message}`,
              trace: finishedTrace,
            },
          ]);
        } else if (msg.type === "trace_update") {
          setTraceSteps((prev) => {
            const next = [...prev, msg.step];
            traceStepsRef.current = next;
            return next;
          });
          window.dispatchEvent(new CustomEvent("trace_update", { detail: msg }));
        }
      },
      (status) => setWsStatus(status),
    );

    return () => {
      wsClient.close();
    };
  }, [sessionId]);

  useEffect(() => {
    if (chatHistoryRef.current) {
      chatHistoryRef.current.scrollTop = chatHistoryRef.current.scrollHeight;
    }
  }, [messages, traceSteps]);

  const handleSendText = () => {
    const trimmedInput = inputText.trim();
    if (!trimmedInput || isProcessing) return;

    if (trimmedInput.length < 2) {
      setMessages((prev) => [
        ...prev,
        { id: Date.now().toString(), sender: "user", text: inputText },
        { id: (Date.now() + 1).toString(), sender: "system", text: "I didn't quite catch that. Could you ask a full question?" }
      ]);
      setInputText("");
      return;
    }

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
      <div className="chat-panel-header">
        <div className="cph-title">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{color: "var(--color-primary)"}}>
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
          </svg>
          Assistant
        </div>
        <button className="btn-new-chat" onClick={handleNewChat} aria-label="Start new chat">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          New Chat
        </button>
      </div>

      <div className="chat-history" ref={chatHistoryRef}>
        {messages.map((m) => (
          <div key={m.id} className={`chat-message ${m.sender}`}>
            {m.sender === "system" ? (
              <div className="sys-msg-container">
                <div className="sys-avatar">
                  <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"></circle>
                    <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                    <line x1="9" y1="9" x2="9.01" y2="9"></line>
                    <line x1="15" y1="9" x2="15.01" y2="9"></line>
                  </svg>
                </div>
                <div className="sys-content">
                  {m.verdict && (
                    <div className={`verdict-banner ${m.verdict}`} aria-label={`Verdict: ${m.verdict}`}>
                      {m.verdict}
                    </div>
                  )}
                  <div>{m.text}</div>
                  {m.trace && m.trace.length > 0 && (
                    // Kept, not discarded, after the answer lands — collapsed
                    // by default so it doesn't compete with the answer, but a
                    // click reopens the same trace FR-UI-3 asks to be visible.
                    <details className="trace-disclosure">
                      <summary>Agent trace ({m.trace.length} step{m.trace.length === 1 ? "" : "s"})</summary>
                      <div className="trace-progress">
                        {m.trace.map((step, i) => (
                          <div key={i} className="trace-step">{step}</div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              </div>
            ) : (
              <div>{m.text}</div>
            )}
          </div>
        ))}
        {isProcessing && (
          <div className="chat-message system">
             <div className="sys-msg-container">
                <div className="sys-avatar">
                  <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"></circle>
                    <path d="M8 14s1.5 2 4 2 4-2 4-2"></path>
                    <line x1="9" y1="9" x2="9.01" y2="9"></line>
                    <line x1="15" y1="9" x2="15.01" y2="9"></line>
                  </svg>
                </div>
                <div className="sys-content">
                  <div className="trace-progress" aria-live="polite" aria-label="Agent progress">
                    <div className="trace-label thinking-dots">Thinking</div>
                    {traceSteps.map((step, i) => (
                      <div key={i} className="trace-step">{step}</div>
                    ))}
                  </div>
                </div>
              </div>
          </div>
        )}
      </div>

      {wsStatus === "closed" && (
        <div className="ws-status-banner" role="status">
          Reconnecting… your next message will send as soon as the connection is back.
        </div>
      )}

      <div className="chat-input-area">
        <div className="chat-input-container">
          <button
            className={`btn-icon ${isRecording ? "recording" : ""}`}
            onClick={toggleRecording}
            disabled={isProcessing}
            aria-label={isRecording ? "Stop recording" : "Start recording"}
          >
            {isRecording ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12"></rect>
              </svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"></path>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
                <line x1="12" y1="19" x2="12" y2="22"></line>
              </svg>
            )}
          </button>
          
          <input
            type="text"
            className="chat-input"
            placeholder="Ask ORCA a question..."
            value={inputText}
            disabled={isProcessing}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSendText()}
            aria-label="Message input"
          />
          
          <button 
            className={`btn-icon btn-send ${isProcessing ? "stop-btn" : ""}`} 
            onClick={isProcessing ? handleStopProcess : handleSendText} 
            aria-label={isProcessing ? "Stop processing" : "Send message"}
          >
            {isProcessing ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12"></rect>
              </svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"></line>
                <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
              </svg>
            )}
          </button>
        </div>
      </div>
    </section>
  );
}
