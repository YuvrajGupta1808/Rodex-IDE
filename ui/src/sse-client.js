/**
 * SSE client with automatic reconnect and lastEventId replay.
 */
export class SSEClient {
  constructor(sessionId, onEvent, onStatusChange) {
    this.sessionId = sessionId;
    this.onEvent = onEvent;
    this.onStatusChange = onStatusChange;
    this._es = null;
    this._lastEventId = 0;
    this._retryDelay = 1000;
    this._closed = false;
  }

  connect() {
    if (this._closed) return;
    const url = `/api/stream/${this.sessionId}?lastEventId=${this._lastEventId}`;
    this._es = new EventSource(url);
    this.onStatusChange?.('connecting');

    this._es.onopen = () => {
      this._retryDelay = 1000;
      this.onStatusChange?.('connected');
    };

    this._es.onmessage = (e) => {
      let event;
      try {
        event = JSON.parse(e.data);
      } catch (_) {
        return;   // keepalive comments and malformed frames are ignored
      }
      // The server replays *from* this index, so resume at the next one.
      const id = parseInt(e.lastEventId, 10);
      if (!Number.isNaN(id)) this._lastEventId = id + 1;
      this.onEvent(event);
    };

    this._es.onerror = () => {
      this._es.close();
      if (this._closed) return;
      this.onStatusChange?.('reconnecting');
      setTimeout(() => {
        this._retryDelay = Math.min(this._retryDelay * 2, 16000);
        this.connect();
      }, this._retryDelay);
    };
  }

  close() {
    this._closed = true;
    this._es?.close();
    this.onStatusChange?.('closed');
  }
}
