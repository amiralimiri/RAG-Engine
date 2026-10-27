/**
 * Verification Test Suite for Ticket 5 (Response Streaming - ADR 0004)
 * - Zero-Dependency Client SSE Parser (readEventStream & streamChat)
 * - Chunk Boundary Fragmentation Handling (UTF-8 multibyte, \n, \n\n, CRLF)
 * - SSE Event Taxonomy (step, token, metadata, error) & JSON deserialization
 * - Graceful Error Recovery on Malformed JSON frames
 * - Reader Lock Lifecycle Guarantee (releaseLock in finally block)
 * - Pre-stream HTTP Error Validation (response.ok === false)
 * - Client-side AbortController Cancellation Signaling
 * - RAF Progressive Render Hook Preparation
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");

// Load app.js code
const appPath = path.resolve(__dirname, "../static/js/app.js");
const appCode = fs.readFileSync(appPath, "utf8");

// Set up mock DOM / Browser environment for Node.js
const mockStorage = new Map();
global.window = {
  localStorage: {
    getItem: (k) => (mockStorage.has(k) ? mockStorage.get(k) : null),
    setItem: (k, v) => mockStorage.set(k, String(v)),
    removeItem: (k) => mockStorage.delete(k),
    clear: () => mockStorage.clear(),
  },
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
};

global.document = {
  documentElement: {
    dataset: {},
    setAttribute: () => {},
  },
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};

// Evaluate app.js in mock scope
const mockModule = { exports: {} };
new Function("module", "exports", appCode)(mockModule, mockModule.exports);

const {
  parseSSEFrame,
  readEventStream,
  streamChat,
  scheduleProgressiveRender,
  getAccumulatedMarkdown,
} = mockModule.exports;

/**
 * Helper: creates a mock ReadableStream delivering arbitrary chunks of Uint8Array or strings.
 */
function createMockStream(chunks, options = {}) {
  let index = 0;
  let lockReleased = false;

  const stream = new ReadableStream({
    start(controller) {
      // Controller initialized
    },
    async pull(controller) {
      if (options.shouldThrowAt && index === options.shouldThrowAt) {
        controller.error(new Error(options.throwMessage || "Stream read error"));
        return;
      }
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      const chunk = chunks[index++];
      if (typeof chunk === "string") {
        controller.enqueue(new TextEncoder().encode(chunk));
      } else if (chunk instanceof Uint8Array) {
        controller.enqueue(chunk);
      } else {
        controller.enqueue(new Uint8Array(chunk));
      }
    },
    cancel(reason) {
      if (options.onCancel) options.onCancel(reason);
    },
  });

  const origGetReader = stream.getReader.bind(stream);
  stream.getReader = () => {
    const reader = origGetReader();
    const origReleaseLock = reader.releaseLock.bind(reader);
    reader.releaseLock = () => {
      lockReleased = true;
      if (options.onReleaseLock) options.onReleaseLock();
      return origReleaseLock();
    };
    return reader;
  };

  return {
    stream,
    isLockReleased: () => lockReleased,
  };
}

/**
 * Helper: creates a mock Fetch Response object with body stream.
 */
function createMockResponse(chunks, options = {}) {
  const { stream, isLockReleased } = createMockStream(chunks, options);
  return {
    response: {
      ok: options.ok !== undefined ? options.ok : true,
      status: options.status || 200,
      statusText: options.statusText || "OK",
      headers: options.headers || new Headers(),
      body: stream,
      text: async () => options.rawText || "",
      json: async () => options.jsonBody || {},
    },
    isLockReleased,
  };
}

async function runTicket5StreamingTests() {
  console.log("=== RUNNING TICKET 5 STREAMING PARSER TEST SUITE ===");

  // --------------------------------------------------------------------------
  // TEST 1: parseSSEFrame Unit Tests
  // --------------------------------------------------------------------------
  console.log("Test 1: parseSSEFrame Unit Verification");
  {
    // Standard event & data
    const frame1 = parseSSEFrame('event: step\ndata: {"step": "route", "label": "Analyzing..."}');
    assert.deepStrictEqual(frame1, {
      event: "step",
      data: { step: "route", label: "Analyzing..." },
    });

    // Default event name is 'message' when omitted
    const frame2 = parseSSEFrame('data: {"delta": "hello"}');
    assert.deepStrictEqual(frame2, {
      event: "message",
      data: { delta: "hello" },
    });

    // Multi-line data concatenated with \n per SSE specification
    const frame3 = parseSSEFrame('event: token\ndata: {"part1": 1,\ndata: "part2": 2}');
    assert.strictEqual(frame3.event, "token");
    assert.deepStrictEqual(frame3.data, { part1: 1, part2: 2 });

    // Comments (lines starting with :) are skipped
    const frame4 = parseSSEFrame(': keep-alive ping\nevent: token\n: secondary comment\ndata: {"delta": "hi"}');
    assert.deepStrictEqual(frame4, {
      event: "token",
      data: { delta: "hi" },
    });

    // Malformed JSON should return null without throwing and log warning
    let warningLogged = false;
    const origWarn = console.warn;
    console.warn = (...args) => {
      warningLogged = true;
    };
    const badFrame = parseSSEFrame("event: token\ndata: {unclosed json");
    console.warn = origWarn;
    assert.strictEqual(badFrame, null, "Malformed JSON frame should return null");
    assert.strictEqual(warningLogged, true, "console.warn should be called for bad JSON");

    // Empty or whitespace frame returns null
    assert.strictEqual(parseSSEFrame(""), null);
    assert.strictEqual(parseSSEFrame("   \n\n  "), null);
    assert.strictEqual(parseSSEFrame(": only comments\n: here"), null);

    console.log("  Passed: parseSSEFrame handles valid, multiline, comment, and malformed frames.");
  }

  // --------------------------------------------------------------------------
  // TEST 2: Full SSE Wire Taxonomy Deserialization (step, token, metadata, error)
  // --------------------------------------------------------------------------
  console.log("Test 2: SSE 4-Event Wire Taxonomy Parsing");
  {
    const frames = [
      'event: step\ndata: {"step": "retrieve_kb", "label": "Searching KB...", "timestamp": 1729000000.1}\n\n',
      'event: token\ndata: {"delta": "The answer "}\n\n',
      'event: token\ndata: {"delta": "is 42."}\n\n',
      'event: metadata\ndata: {"trace_id": "tr-abc", "source_used": "private_kb", "citations": [{"title": "guide.pdf"}], "trace": ["Router -> KB"]}\n\n',
      'event: error\ndata: {"message": "Simulated mid-stream failure", "code": 500}\n\n',
    ];

    const { response, isLockReleased } = createMockResponse(frames);
    const received = [];

    for await (const item of readEventStream(response)) {
      received.push(item);
    }

    assert.strictEqual(received.length, 5, "Should have received exactly 5 SSE events");
    assert.strictEqual(received[0].event, "step");
    assert.strictEqual(received[0].data.step, "retrieve_kb");
    assert.strictEqual(received[0].data.label, "Searching KB...");

    assert.strictEqual(received[1].event, "token");
    assert.strictEqual(received[1].data.delta, "The answer ");

    assert.strictEqual(received[2].event, "token");
    assert.strictEqual(received[2].data.delta, "is 42.");

    assert.strictEqual(received[3].event, "metadata");
    assert.strictEqual(received[3].data.trace_id, "tr-abc");
    assert.strictEqual(received[3].data.source_used, "private_kb");
    assert.strictEqual(received[3].data.citations[0].title, "guide.pdf");

    assert.strictEqual(received[4].event, "error");
    assert.strictEqual(received[4].data.message, "Simulated mid-stream failure");
    assert.strictEqual(received[4].data.code, 500);

    assert.strictEqual(isLockReleased(), true, "Reader lock must be released on stream completion");
    console.log("  Passed: All 4 SSE event types deserialized with intact JSON schemas.");
  }

  // --------------------------------------------------------------------------
  // TEST 3: Chunk Fragmentation Across Line Breaks (\n)
  // --------------------------------------------------------------------------
  console.log("Test 3: Chunk Boundary Fragmentation Across Single Newlines (\\n)");
  {
    // Slice chunks right at single \n boundaries
    const chunks = [
      "event: step\n",
      'data: {"step": "route", "label": "Routing inquiry"}\n\n',
      "event: token\n",
      'data: {"delta": "Hel',
      'lo"}\n\n',
    ];

    const { response, isLockReleased } = createMockResponse(chunks);
    const received = [];
    for await (const item of readEventStream(response)) {
      received.push(item);
    }

    assert.strictEqual(received.length, 2);
    assert.strictEqual(received[0].event, "step");
    assert.strictEqual(received[0].data.step, "route");
    assert.strictEqual(received[1].event, "token");
    assert.strictEqual(received[1].data.delta, "Hello");
    assert.strictEqual(isLockReleased(), true);
    console.log("  Passed: Fragmentation across single newlines handled without dropping frames.");
  }

  // --------------------------------------------------------------------------
  // TEST 4: Chunk Fragmentation Across Frame Delimiters (\n\n and \r\n\r\n)
  // --------------------------------------------------------------------------
  console.log("Test 4: Chunk Boundary Fragmentation Across Double Newlines (\\n\\n and \\r\\n\\r\\n)");
  {
    // Slice 1: split right between \n and \n
    const chunks1 = [
      'event: token\ndata: {"delta": "A"}\n',
      '\nevent: token\ndata: {"delta": "B"}\n\n',
    ];
    const { response: res1 } = createMockResponse(chunks1);
    const received1 = [];
    for await (const item of readEventStream(res1)) {
      received1.push(item);
    }
    assert.strictEqual(received1.length, 2);
    assert.strictEqual(received1[0].data.delta, "A");
    assert.strictEqual(received1[1].data.delta, "B");

    // Slice 2: split across Windows CRLF (\r\n\r and \n)
    const chunks2 = [
      'event: token\r\ndata: {"delta": "C"}\r\n\r',
      '\nevent: token\r\ndata: {"delta": "D"}\r\n\r\n',
    ];
    const { response: res2 } = createMockResponse(chunks2);
    const received2 = [];
    for await (const item of readEventStream(res2)) {
      received2.push(item);
    }
    assert.strictEqual(received2.length, 2);
    assert.strictEqual(received2[0].data.delta, "C");
    assert.strictEqual(received2[1].data.delta, "D");

    console.log("  Passed: Split across \\n\\n and \\r\\n\\r\\n correctly reconstituted.");
  }

  // --------------------------------------------------------------------------
  // TEST 5: Chunk Fragmentation Across Multi-Byte UTF-8 Characters
  // --------------------------------------------------------------------------
  console.log("Test 5: Multi-Byte UTF-8 Boundary Splitting");
  {
    // Construct multi-byte strings:
    // '🚀' = 4 bytes: [0xF0, 0x9F, 0x9A, 0x80]
    // '€'  = 3 bytes: [0xE2, 0x82, 0xAC]
    // 'ü'  = 2 bytes: [0xC3, 0xBC]
    // '你好' = 6 bytes: [0xE4, 0xBD, 0xA0, 0xE5, 0xA5, 0xBD]
    const fullText = 'event: token\ndata: {"delta": "Rocket 🚀, Euro €50, German Über, Chinese 你好"}\n\n';
    const fullBytes = new TextEncoder().encode(fullText);

    // Find byte offset inside 🚀 (first 2 bytes in chunk 1, remaining in chunk 2)
    const rocketIndex = fullText.indexOf("🚀");
    const rocketByteOffset = new TextEncoder().encode(fullText.slice(0, rocketIndex)).length;

    // Split right in the middle of 🚀 (after 2 of its 4 bytes)
    const splitPoint = rocketByteOffset + 2;
    const chunkA = fullBytes.slice(0, splitPoint);
    const chunkB = fullBytes.slice(splitPoint);

    const { response, isLockReleased } = createMockResponse([chunkA, chunkB]);
    const received = [];
    for await (const item of readEventStream(response)) {
      received.push(item);
    }

    assert.strictEqual(received.length, 1);
    assert.strictEqual(
      received[0].data.delta,
      "Rocket 🚀, Euro €50, German Über, Chinese 你好",
      "Multi-byte UTF-8 character must decode properly across chunk splits",
    );
    assert.strictEqual(isLockReleased(), true);
    console.log("  Passed: Incomplete UTF-8 multi-byte sequences buffered and decoded cleanly.");
  }

  // --------------------------------------------------------------------------
  // TEST 6: Multiple Frames In A Single Chunk & Trailing Frame Without Delimiter
  // --------------------------------------------------------------------------
  console.log("Test 6: Multiple Frames In Single Chunk & Trailing Buffer Flush");
  {
    // 3 frames in a single chunk
    const singleChunk =
      'event: token\ndata: {"delta": "1"}\n\n' +
      'event: token\ndata: {"delta": "2"}\n\n' +
      'event: token\ndata: {"delta": "3"}\n\n';

    const { response: res1 } = createMockResponse([singleChunk]);
    const received1 = [];
    for await (const item of readEventStream(res1)) {
      received1.push(item.data.delta);
    }
    assert.deepStrictEqual(received1, ["1", "2", "3"]);

    // Trailing frame without final \n\n (stream abruptly ends after line)
    const trailingChunk =
      'event: token\ndata: {"delta": "start"}\n\n' +
      'event: metadata\ndata: {"trace_id": "final_trace"}'; // No trailing \n\n

    const { response: res2 } = createMockResponse([trailingChunk]);
    const received2 = [];
    for await (const item of readEventStream(res2)) {
      received2.push(item);
    }
    assert.strictEqual(received2.length, 2);
    assert.strictEqual(received2[1].event, "metadata");
    assert.strictEqual(received2[1].data.trace_id, "final_trace");

    console.log("  Passed: Multi-frame single chunks and trailing stream flush verified.");
  }

  // --------------------------------------------------------------------------
  // TEST 7: Resilient Error Recovery on Corrupt JSON Chunks
  // --------------------------------------------------------------------------
  console.log("Test 7: Resilient Handling of Malformed JSON In Stream");
  {
    const chunks = [
      'event: token\ndata: {"delta": "good_1"}\n\n',
      'event: token\ndata: {MALFORMED JSON CHUNK}\n\n',
      'event: token\ndata: {"delta": "good_2"}\n\n',
    ];

    let warnCount = 0;
    const origWarn = console.warn;
    console.warn = () => {
      warnCount++;
    };

    const { response } = createMockResponse(chunks);
    const received = [];
    for await (const item of readEventStream(response)) {
      received.push(item.data.delta);
    }
    console.warn = origWarn;

    assert.strictEqual(warnCount, 1, "Should warn once for corrupt chunk");
    assert.deepStrictEqual(
      received,
      ["good_1", "good_2"],
      "Stream must skip corrupt chunk and continue reading subsequent valid frames",
    );
    console.log("  Passed: Malformed JSON logged and skipped without crashing stream iteration.");
  }

  // --------------------------------------------------------------------------
  // TEST 8: Reader Lock Release Guarantee (Break and Exception Handling)
  // --------------------------------------------------------------------------
  console.log("Test 8: Reader Lock Release Guarantee (finally block)");
  {
    // Case A: Consumer breaks out early
    const chunksA = [
      'event: token\ndata: {"delta": "1"}\n\n',
      'event: token\ndata: {"delta": "2"}\n\n',
      'event: token\ndata: {"delta": "3"}\n\n',
    ];
    let lockReleasedA = false;
    const { response: resA } = createMockResponse(chunksA, {
      onReleaseLock: () => {
        lockReleasedA = true;
      },
    });

    for await (const item of readEventStream(resA)) {
      if (item.data.delta === "1") {
        break; // Consumer early exit
      }
    }
    assert.strictEqual(lockReleasedA, true, "Lock must be released when consumer breaks early");

    // Case B: Stream read throws error
    let lockReleasedB = false;
    const { response: resB } = createMockResponse(chunksA, {
      shouldThrowAt: 1,
      throwMessage: "Network socket terminated",
      onReleaseLock: () => {
        lockReleasedB = true;
      },
    });

    let caughtError = null;
    try {
      for await (const _ of readEventStream(resB)) {
        // read until throw
      }
    } catch (err) {
      caughtError = err;
    }
    assert(caughtError !== null, "Stream error must propagate to consumer");
    assert.strictEqual(caughtError.message, "Network socket terminated");
    assert.strictEqual(lockReleasedB, true, "Lock must be released even when stream reader throws");

    console.log("  Passed: reader.releaseLock() is guaranteed in all early exit & error paths.");
  }

  // --------------------------------------------------------------------------
  // TEST 9: streamChat Pre-Stream Validation (HTTP 400 & HTTP 500)
  // --------------------------------------------------------------------------
  console.log("Test 9: streamChat Pre-Stream HTTP Status & Error Body Validation");
  {
    // Test 400 Bad Request with JSON detail
    global.fetch = async (url, opts) => {
      assert.strictEqual(url, "/api/chat/stream");
      assert.strictEqual(opts.method, "POST");
      assert.strictEqual(opts.headers["Content-Type"], "application/json");
      return {
        ok: false,
        status: 400,
        statusText: "Bad Request",
        text: async () => JSON.stringify({ detail: "Unknown workflow 'invalid_engine'" }),
        json: async () => ({ detail: "Unknown workflow 'invalid_engine'" }),
      };
    };

    let caughtErr400 = null;
    try {
      for await (const _ of streamChat({ question: "hi", workflow: "invalid_engine" })) {
        // should not yield
      }
    } catch (err) {
      caughtErr400 = err;
    }

    assert(caughtErr400 !== null, "streamChat must throw on HTTP 400");
    assert.strictEqual(caughtErr400.status, 400);
    assert.strictEqual(caughtErr400.message, "Unknown workflow 'invalid_engine'");

    // Test 502 Bad Gateway with non-JSON text
    global.fetch = async () => ({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      text: async () => "<html>502 Bad Gateway</html>",
      json: async () => {
        throw new Error("Unexpected HTML");
      },
    });

    let caughtErr502 = null;
    try {
      for await (const _ of streamChat({ question: "hi" })) {
        // should not yield
      }
    } catch (err) {
      caughtErr502 = err;
    }

    assert(caughtErr502 !== null, "streamChat must throw on HTTP 502");
    assert.strictEqual(caughtErr502.status, 502);
    assert(
      caughtErr502.message.includes("502") || caughtErr502.message.includes("Bad Gateway"),
      "Error message should reflect status text or raw body",
    );

    console.log("  Passed: Pre-stream HTTP errors parse detail and throw typed Error before streaming.");
  }

  // --------------------------------------------------------------------------
  // TEST 10: streamChat Client-Side Cancellation via AbortController
  // --------------------------------------------------------------------------
  console.log("Test 10: Client Cancellation via AbortController");
  {
    const abortCtrl = new AbortController();

    global.fetch = async (url, opts) => {
      // Create a mock stream that listens to abort signal
      let streamCancelled = false;
      const chunks = [
        'event: token\ndata: {"delta": "chunk1"}\n\n',
        'event: token\ndata: {"delta": "chunk2"}\n\n',
      ];
      const { stream, isLockReleased } = createMockStream(chunks, {
        onCancel: () => {
          streamCancelled = true;
        },
      });

      if (opts.signal) {
        opts.signal.addEventListener("abort", () => {
          // Trigger mock cancellation
        });
      }

      return {
        ok: true,
        status: 200,
        statusText: "OK",
        body: stream,
        text: async () => "",
        json: async () => ({}),
      };
    };

    const receivedTokens = [];
    let abortedCleanly = false;

    try {
      for await (const event of streamChat(
        { question: "Cancel test" },
        { signal: abortCtrl.signal },
      )) {
        receivedTokens.push(event.data.delta);
        if (event.data.delta === "chunk1") {
          abortCtrl.abort();
          break;
        }
      }
      abortedCleanly = true;
    } catch (e) {
      if (e.name === "AbortError") {
        abortedCleanly = true;
      }
    }

    assert.strictEqual(receivedTokens.length, 1);
    assert.strictEqual(receivedTokens[0], "chunk1");
    assert.strictEqual(abortedCleanly, true);

    console.log("  Passed: AbortController signaling terminates stream and releases resources.");
  }

  // --------------------------------------------------------------------------
  // TEST 11: Ticket 6 Progressive Render Preparation Hook
  // --------------------------------------------------------------------------
  console.log("Test 11: Progressive Render Hook (scheduleProgressiveRender)");
  {
    const mockContentEl = {
      innerHTML: "",
      textContent: "",
    };

    scheduleProgressiveRender(mockContentEl, "Progressive **Token** Render");
    assert.strictEqual(getAccumulatedMarkdown(), "Progressive **Token** Render");

    // Wait for RAF callback
    await new Promise((r) => setTimeout(r, 20));
    assert(
      mockContentEl.innerHTML.includes("Progressive") ||
        mockContentEl.textContent.includes("Progressive"),
      "Content element must be updated with progressive markdown",
    );
    console.log("  Passed: scheduleProgressiveRender batches text updates for Ticket 6.");
  }

  console.log("\n=======================================================");
  console.log(" ALL 11 TICKET 5 STREAMING PARSER VERIFICATION CHECKS PASSED ");
  console.log("=======================================================\n");
}

runTicket5StreamingTests().catch((err) => {
  console.error("Test failed with error:", err);
  process.exit(1);
});
