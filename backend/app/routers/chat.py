"""Conversations with Dr MedNama.

Moved verbatim from app/main.py (routes keep their paths)."""

import json
from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.generation import generate_answer
from app.models import Chunk, User, ChatConversation, ChatMessage
from app.auth import require_student_or_admin
from app.deps import ChatQueryRequest, get_db, logger

router = APIRouter()

# ======================== CONVERSATIONAL CHAT HISTORY ========================

def _run_chat_turn(db: Session, req: "ChatQueryRequest", user_id: int, on_stage=None) -> dict:
    """One chat turn: resolve/create the conversation, answer with history, persist both messages."""
    conv_id = req.conversation_id
    if not conv_id:
        # Create a new conversation and auto-title based on the query prefix
        words = req.query.strip().split()
        title_text = " ".join(words[:6]) + ("..." if len(words) > 6 else "")
        conv = ChatConversation(user_id=user_id, title=title_text or "New Conversation")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        conv_id = conv.id
    else:
        conv = db.query(ChatConversation).filter(
            ChatConversation.id == conv_id,
            ChatConversation.user_id == user_id
        ).first()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation session not found.")

    # Retrieve message history to maintain conversational memory (last 10 turns max)
    db_messages = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conv_id
    ).order_by(ChatMessage.created_at.asc()).all()

    history = []
    for msg in db_messages[-10:]:
        if msg.role == "user":
            history.append({"role": "user", "content": msg.content or ""})
        elif msg.role == "ai":
            ans_text = ""
            if msg.answer_json:
                try:
                    ans_text = json.loads(msg.answer_json).get("answer_markdown", "")
                except (ValueError, AttributeError):
                    pass
            history.append({"role": "assistant", "content": ans_text or msg.content or ""})

    answer_dict = generate_answer(
        session=db,
        query=req.query,
        confidence_threshold=req.confidence_threshold,
        history=history,
        book_id=req.book_id,
        chapter=req.chapter,
        level=req.level,
        on_stage=on_stage,
    )

    db.add(ChatMessage(conversation_id=conv_id, role="user", content=req.query))
    db.add(ChatMessage(
        conversation_id=conv_id,
        role="ai",
        content=answer_dict.get("answer_markdown", ""),
        answer_json=json.dumps(answer_dict),
    ))
    conv.updated_at = datetime.utcnow()
    db.commit()

    return {
        "conversation_id": conv_id,
        "conversation_title": conv.title,
        "answer": answer_dict
    }

@router.post("/api/chat/query")
def chat_query_endpoint(
    req: ChatQueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Conversational RAG query answering endpoint that tracks message logs in DB and maintains LLM memory context."""
    return _run_chat_turn(db, req, current_user.id)

SSE_HEARTBEAT_S = 3.0

def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

@router.post("/api/chat/query/stream")
def chat_query_stream_endpoint(
    req: ChatQueryRequest,
    current_user: User = Depends(require_student_or_admin)
):
    """Same as /api/chat/query, streamed as Server-Sent Events.

    Progress events ("stage") arrive as the pipeline advances and a comment
    heartbeat is sent every few seconds, so no proxy or browser ever sees an
    idle connection while retrieval and the AI call run. The final event is
    "answer" (the /api/chat/query payload) or "error" ({"detail", "status"}).
    """
    import queue
    import threading

    user_id = current_user.id
    events: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def work() -> None:
        # Own session: request-scoped dependencies are closed before a
        # streaming body runs.
        db = SessionLocal()
        try:
            result = _run_chat_turn(db, req, user_id, on_stage=lambda stage: events.put(("stage", {"stage": stage})))
            events.put(("answer", result))
        except HTTPException as e:
            events.put(("error", {"detail": e.detail, "status": e.status_code}))
        except Exception as e:
            logger.exception("Streaming chat turn failed")
            events.put(("error", {"detail": f"Internal error: {e}", "status": 500}))
        finally:
            db.close()

    def stream():
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        yield _sse("stage", {"stage": "received"})
        while True:
            try:
                event, data = events.get(timeout=SSE_HEARTBEAT_S)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            yield _sse(event, data)
            if event in ("answer", "error"):
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )

@router.get("/api/chat/source/{chunk_id}")
def get_chat_source_full(
    chunk_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns the full text of a retrieved source chunk for the expandable sources panel."""
    chunk = db.query(Chunk).filter(Chunk.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found.")
    return {
        "chunk_id": chunk.id,
        "book_title": chunk.book.title if chunk.book else "Unknown Textbook",
        "chapter": chunk.chapter,
        "page_number": chunk.page_number,
        "content": chunk.content,
    }

@router.get("/api/chat/conversations")
def get_user_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Returns the user's active chat conversations list ordered by recent updates."""
    conversations = db.query(ChatConversation).filter(
        ChatConversation.user_id == current_user.id
    ).order_by(ChatConversation.updated_at.desc()).all()
    
    return [
        {
            "id": c.id,
            "title": c.title,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None
        }
        for c in conversations
    ]

@router.get("/api/chat/conversations/{conversation_id}")
def get_conversation_history(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Retrieves all chat messages for a specific conversation session."""
    conv = db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.user_id == current_user.id
    ).first()
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
        
    db_messages = db.query(ChatMessage).filter(
        ChatMessage.conversation_id == conversation_id
    ).order_by(ChatMessage.created_at.asc()).all()
    
    formatted_messages = []
    for msg in db_messages:
        answer_data = None
        if msg.answer_json:
            try:
                answer_data = json.loads(msg.answer_json)
            except:
                pass
                
        formatted_messages.append({
            "id": f"msg-{msg.id}",
            "type": "user" if msg.role == "user" else ("error" if msg.role == "error" else "ai"),
            "content": msg.content,
            "answer": answer_data,
            "timestamp": msg.created_at.strftime("%I:%M %p") if msg.created_at else None
        })
        
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": formatted_messages
    }

@router.delete("/api/chat/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_student_or_admin)
):
    """Deletes a chat conversation thread and all its messages."""
    conv = db.query(ChatConversation).filter(
        ChatConversation.id == conversation_id,
        ChatConversation.user_id == current_user.id
    ).first()
    
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
        
    db.delete(conv)
    db.commit()
    
    return {"message": "Conversation deleted successfully."}
