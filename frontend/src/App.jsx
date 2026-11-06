import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  askQuestion,
  fetchExamples,
  fetchMeta,
  fetchReady,
} from "./api";

const STATUS_META = {
  FOUND: {
    label: "Данные найдены",
    hint: "Ответ рассчитан по активному набору данных.",
    tone: "found",
  },
  NOT_FOUND: {
    label: "Не найдено",
    hint: "Запрос выполнен, но совпадений в наборе данных нет.",
    tone: "not-found",
  },
  NEEDS_CLARIFICATION: {
    label: "Нужно уточнение",
    hint: "Агент не угадывает: уточните сущность или условие.",
    tone: "clarify",
  },
  UNSUPPORTED: {
    label: "Не поддерживается",
    hint: "Этот вопрос нельзя достоверно выполнить на текущей схеме.",
    tone: "unsupported",
  },
  UNVERIFIED: {
    label: "Не удалось подтвердить",
    hint: "Ошибка или проверка не пройдена. Это не означает «не найдено».",
    tone: "unverified",
  },
  GENERAL: {
    label: "Общий ответ",
    hint: "Ответ из общих знаний. Он не проверялся по реестру сделок.",
    tone: "general",
  },
  GENERAL_UNAVAILABLE: {
    label: "AI временно недоступен",
    hint: "Техническая ошибка AI-провайдера, а не отсутствие данных в реестре.",
    tone: "unverified",
  },
};

const SOURCE_LABELS = {
  deals: "Сделки",
  contracts: "Контракты",
  participants: "Участники",
  brokers: "Брокеры",
  regions: "Регионы",
  region_aliases: "Псевдонимы регионов",
  usd_rates: "Курсы USD",
  dataset_meta: "Метаданные набора",
};

const FIELD_LABELS = {
  deal_date: "Дата сделки",
  deal_no: "Номер сделки",
  contract_no: "Номер контракта",
  seller_inn: "ИНН продавца",
  buyer_inn: "ИНН покупателя",
  seller_broker_code: "Код брокера продавца",
  buyer_broker_code: "Код брокера покупателя",
  warehouse_region: "Регион склада",
  seller_company_region: "Регион продавца",
  buyer_company_region: "Регион покупателя",
  product_name: "Товар",
  deal_product_name: "Товар в сделке",
  hs_code: "Код ТН ВЭД",
  status: "Статус",
  trade_type: "Тип торгов",
  contract_type: "Тип контракта",
  unit_name: "Единица",
  currency: "Валюта",
  period: "Период",
  year: "Год",
  month: "Месяц",
};

const UI_TEXT = {
  title: "UZEX Data Agent",
  subtitle:
    "Проверяемые ответы по загруженному набору данных. Каждый факт рассчитывается в PostgreSQL.",
  emptyTitle: "Задайте вопрос по данным",
  emptyBody:
    "Можно спросить о количестве сделок, суммах, объёмах, средних, рейтингах, сравнениях и точных записях. Ответ будет содержать основание расчёта.",
  inputLabel: "Вопрос к данным",
  placeholder: "Например: Сколько сделок было в 2024 году?",
  submit: "Спросить",
  sending: "Выполняется запрос…",
  history: "История диалога",
  clear: "Очистить историю",
  evidence: "Основание ответа",
  technical: "Технические детали",
  examples: "Примеры вопросов",
  exampleBadge: "пример",
  dataset: "Набор данных",
  cutoff: "Срез",
  loaded: "Загружено сделок",
  language: "Язык",
  clarificationOptions: "Варианты уточнения",
  insertOption: "Вставить в вопрос",
  structured: "Структурированные данные",
  nullValue: "NULL",
  noValue: "Значение отсутствует (NULL)",
};

function formatInteger(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return String(value ?? "");
  }
  return new Intl.NumberFormat("ru-RU").format(value);
}

function languageBadge(response) {
  if (!response) {
    return "RU · UZ";
  }
  if (response.language === "uz") {
    return response.script === "cyrillic" ? "UZ · кириллица" : "UZ · латиница";
  }
  return "RU";
}

function groupTitle(row) {
  if (row.label) {
    return row.label;
  }
  const values = Object.values(row.group || {}).filter(
    (value) => value !== null && value !== undefined && value !== "",
  );
  return values.length ? values.map(String).join(" / ") : "—";
}

function displayValue(value) {
  if (value === null || value === undefined) {
    return UI_TEXT.nullValue;
  }
  if (typeof value === "number") {
    return formatInteger(value);
  }
  return String(value);
}

function renderFilterValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join(" — ");
  }
  if (value && typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value ?? "");
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || {
    label: status || "Ответ",
    tone: "unknown",
  };
  return <span className={`status-badge tone-${meta.tone}`}>{meta.label}</span>;
}

function DataBlock({ data }) {
  if (!data) {
    return null;
  }
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const lookupRows = rows.filter(
    (row) => row.fields && Object.keys(row.fields).length > 0,
  );
  const aggregateRows = rows.filter(
    (row) => !row.fields || Object.keys(row.fields).length === 0,
  );
  const onlyScalarRow =
    aggregateRows.length === 1 &&
    !aggregateRows[0].label &&
    Object.keys(aggregateRows[0].group || {}).length === 0 &&
    data.value !== null &&
    data.value !== undefined;

  return (
    <div className="data-block">
      {data.value !== null && data.value !== undefined && (
        <div className="scalar">
          <span className="scalar-value">{displayValue(data.value)}</span>
          {data.unit && <span className="scalar-suffix">{data.unit}</span>}
          {data.currency && <span className="scalar-suffix">{data.currency}</span>}
        </div>
      )}

      {aggregateRows.length > 0 && !onlyScalarRow && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Показатель</th>
                <th>Значение</th>
                <th>Единица / валюта</th>
              </tr>
            </thead>
            <tbody>
              {aggregateRows.map((row, index) => (
                <tr key={`row-${index}`}>
                  <td>{groupTitle(row)}</td>
                  <td className="numeric">{displayValue(row.value)}</td>
                  <td>{[row.unit, row.currency].filter(Boolean).join(" · ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {lookupRows.length > 0 && (
        <details className="structured" open>
          <summary>{UI_TEXT.structured}</summary>
          {lookupRows.map((row, index) => (
            <dl key={`fields-${index}`} className="field-grid">
              {Object.entries(row.fields).map(([key, value]) => (
                <div className="field" key={key}>
                  <dt>{FIELD_LABELS[key] || key}</dt>
                  <dd>{displayValue(value)}</dd>
                </div>
              ))}
            </dl>
          ))}
        </details>
      )}

      {data.period && <p className="data-note">Период: {data.period}</p>}
      {data.method && <p className="data-note">Метод: {data.method}</p>}
      {data.notes?.length > 0 && (
        <ul className="notes">
          {data.notes.map((note, index) => (
            <li key={`note-${index}`}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EvidenceBlock({ evidence }) {
  if (!evidence) {
    return null;
  }
  const sources = (evidence.source_tables || []).map(
    (table) => SOURCE_LABELS[table] || table,
  );
  return (
    <section className="evidence" aria-label={UI_TEXT.evidence}>
      <h3>{UI_TEXT.evidence}</h3>
      <div className="evidence-summary">
        <div>
          <span className="evidence-label">Источники</span>
          <span>{sources.join(", ") || "—"}</span>
        </div>
        <div>
          <span className="evidence-label">Срез данных</span>
          <span>{evidence.dataset_cutoff || "—"}</span>
        </div>
        <div>
          <span className="evidence-label">Совпало строк</span>
          <span>{formatInteger(evidence.matched_rows)}</span>
        </div>
      </div>
      <details className="technical">
        <summary>{UI_TEXT.technical}</summary>
        <dl className="technical-grid">
          <dt>Метрика</dt>
          <dd>{evidence.metric || "—"}</dd>
          <dt>Версия набора</dt>
          <dd className="mono">{evidence.dataset_version || "—"}</dd>
          <dt>Идентификатор запроса</dt>
          <dd className="mono">{evidence.execution_id || "—"}</dd>
          <dt>Группировка</dt>
          <dd>{(evidence.grouping || []).join(", ") || "—"}</dd>
          {evidence.currency_method && (
            <>
              <dt>Метод валюты</dt>
              <dd>{evidence.currency_method}</dd>
            </>
          )}
          {evidence.filters?.length > 0 && (
            <>
              <dt>Фильтры</dt>
              <dd>
                <ul className="filter-list">
                  {evidence.filters.map((filter, index) => (
                    <li key={`filter-${index}`}>
                      <span className="mono">
                        {FIELD_LABELS[filter.field] || filter.field}
                      </span>{" "}
                      {filter.operator} {renderFilterValue(filter.value)}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
          {evidence.notes?.length > 0 && (
            <>
              <dt>Примечания</dt>
              <dd>
                <ul>
                  {evidence.notes.map((note, index) => (
                    <li key={`evidence-note-${index}`}>{note}</li>
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
      </details>
    </section>
  );
}

function AnswerMessage({ message, onUseOption }) {
  if (message.pending) {
    return (
      <div className="message assistant" aria-live="polite">
        <div className="message-meta">
          <span className="author">UZEX Data Agent</span>
          <span className="pending-dots" aria-label={UI_TEXT.sending}>
            <i />
            <i />
            <i />
          </span>
        </div>
        <p className="pending-text">{UI_TEXT.sending}</p>
      </div>
    );
  }

  if (message.error) {
    return (
      <div className="message assistant error" role="alert">
        <div className="message-meta">
          <span className="author">UZEX Data Agent</span>
        </div>
        <p>{message.error}</p>
      </div>
    );
  }

  const response = message.response;
  const meta = STATUS_META[response.status] || {};
  return (
    <article
      className={`message assistant status-${meta.tone || "unknown"}`}
      aria-live="polite"
    >
      <div className="message-meta">
        <span className="author">UZEX Data Agent</span>
        <span className="language-chip">{languageBadge(response)}</span>
        <StatusBadge status={response.status} />
      </div>
      <p className="status-hint">{meta.hint}</p>
      <p className="answer-text">{response.answer}</p>

      <DataBlock data={response.data} />

      {response.clarification?.options?.length > 0 && (
        <div className="clarification">
          <h3>{UI_TEXT.clarificationOptions}</h3>
          <div className="option-list">
            {response.clarification.options.map((option) => (
              <button
                type="button"
                className="option-button"
                key={option}
                onClick={() => onUseOption(option)}
                title={UI_TEXT.insertOption}
              >
                {option}
              </button>
            ))}
          </div>
        </div>
      )}

      <EvidenceBlock evidence={response.evidence} />

      <div className="message-foot">
        <span className="request-id mono">{response.request_id}</span>
        <span>{`${response.duration_ms} мс`}</span>
      </div>
    </article>
  );
}

function ReadinessBadge({ readiness }) {
  const state = readiness?.state || "loading";
  const label = {
    loading: "Проверяем готовность…",
    ready: "Сервис готов",
    not_ready: "База не готова",
    offline: "Сервер недоступен",
  }[state];
  return (
    <span className={`readiness readiness-${state}`}>
      <i aria-hidden="true" />
      {label}
    </span>
  );
}

function loadHistory() {
  try {
    const raw = window.sessionStorage.getItem("uzex-chat-history");
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(-30) : [];
  } catch {
    return [];
  }
}

function makeId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function App() {
  const [readiness, setReadiness] = useState({ state: "loading", data: null });
  const [meta, setMeta] = useState(null);
  const [examples, setExamples] = useState([]);
  const [messages, setMessages] = useState(loadHistory);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);
  const historyRef = useRef(null);

  useEffect(() => {
    let active = true;
    async function check() {
      try {
        const ready = await fetchReady();
        if (!active) {
          return;
        }
        setReadiness({
          state: ready.status === "ready" ? "ready" : "not_ready",
          data: ready,
        });
      } catch {
        if (active) {
          setReadiness({ state: "offline", data: null });
        }
      }
    }
    check();
    const timer = window.setInterval(check, 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let active = true;
    Promise.allSettled([fetchMeta(), fetchExamples()]).then(([metaResult, examplesResult]) => {
      if (!active) {
        return;
      }
      if (metaResult.status === "fulfilled") {
        setMeta(metaResult.value);
      }
      if (examplesResult.status === "fulfilled") {
        setExamples(examplesResult.value.examples || []);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(
        "uzex-chat-history",
        JSON.stringify(messages.filter((message) => !message.pending).slice(-30)),
      );
    } catch {
      // Storage may be disabled; the in-memory session still works.
    }
  }, [messages]);

  useEffect(() => {
    if (historyRef.current) {
      historyRef.current.scrollTop = historyRef.current.scrollHeight;
    }
  }, [messages]);

  const datasetSummary = useMemo(() => {
    if (!meta || meta.status !== "ready") {
      return null;
    }
    return {
      cutoff: meta.dataset_cutoff,
      totalDeals: meta.total_deals,
      contracts: meta.contracts,
    };
  }, [meta]);

  const submit = useCallback(
    async (event) => {
      event?.preventDefault();
      const text = question.trim();
      if (!text || busy) {
        return;
      }
      setBusy(true);
      setQuestion("");
      const requestId = makeId();
      const userMessage = { id: makeId(), role: "user", text };
      const pendingId = makeId();
      setMessages((current) => [
        ...current,
        userMessage,
        { id: pendingId, role: "assistant", pending: true },
      ]);

      try {
        const response = await askQuestion(text, requestId);
        setMessages((current) =>
          current.map((message) =>
            message.id === pendingId
              ? { id: pendingId, role: "assistant", response }
              : message,
          ),
        );
      } catch (error) {
        let textError = "Не удалось получить ответ. Сервис не подтвердил данные.";
        if (error instanceof ApiError) {
          if (error.code === "RATE_LIMITED") {
            textError = `Слишком много запросов. Повторите через ${error.retryAfter || 60} с.`;
          } else if (error.code === "TIMEOUT") {
            textError = "Сервер не ответил вовремя. Ответ не подтверждён.";
          } else if (error.status === 422) {
            textError = "Проверьте вопрос: он пустой или превышает допустимую длину.";
          } else if (error.code === "NETWORK") {
            textError = "Нет связи с сервером. Проверьте подключение и повторите.";
          }
        }
        setMessages((current) =>
          current.map((message) =>
            message.id === pendingId
              ? { id: pendingId, role: "assistant", error: textError }
              : message,
          ),
        );
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [busy, question],
  );

  const useOption = useCallback((option) => {
    setQuestion((current) => (current ? `${current} ${option}` : option));
    inputRef.current?.focus();
  }, []);

  const clearHistory = useCallback(() => {
    setMessages([]);
    window.sessionStorage.removeItem("uzex-chat-history");
  }, []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            U
          </div>
          <div>
            <h1>{UI_TEXT.title}</h1>
            <p>{UI_TEXT.subtitle}</p>
          </div>
        </div>
        <div className="header-side">
          <ReadinessBadge readiness={readiness} />
          {datasetSummary && (
            <div className="dataset-strip">
              <span>
                {UI_TEXT.cutoff}: <strong>{datasetSummary.cutoff}</strong>
              </span>
              <span>
                {UI_TEXT.loaded}: <strong>{formatInteger(datasetSummary.totalDeals)}</strong>
              </span>
              <span className="language-chip">RU · UZ</span>
            </div>
          )}
        </div>
      </header>

      <main className="layout">
        <section className="chat-panel" aria-label={UI_TEXT.history}>
          <div className="history" ref={historyRef}>
            {messages.length === 0 ? (
              <div className="empty-state">
                <h2>{UI_TEXT.emptyTitle}</h2>
                <p>{UI_TEXT.emptyBody}</p>
                <ul className="empty-points">
                  <li>Ответы только из активного набора PostgreSQL.</li>
                  <li>NULL, отсутствие записей и ошибки показываются по-разному.</li>
                  <li>Уточнение запрашивается вместо догадки.</li>
                </ul>
              </div>
            ) : (
              messages.map((message) =>
                message.role === "user" ? (
                  <div className="message user" key={message.id}>
                    <div className="message-meta">
                      <span className="author">Вы</span>
                    </div>
                    <p className="answer-text">{message.text}</p>
                  </div>
                ) : (
                  <AnswerMessage
                    key={message.id}
                    message={message}
                    onUseOption={useOption}
                  />
                ),
              )
            )}
          </div>

          <form className="composer" onSubmit={submit}>
            <label htmlFor="question">{UI_TEXT.inputLabel}</label>
            <div className="composer-row">
              <textarea
                id="question"
                ref={inputRef}
                rows={2}
                maxLength={1500}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit(event);
                  }
                }}
                placeholder={UI_TEXT.placeholder}
                disabled={busy}
              />
              <button type="submit" disabled={busy || !question.trim()}>
                {busy ? UI_TEXT.sending : UI_TEXT.submit}
              </button>
            </div>
            <div className="composer-foot">
              <span>Enter — отправить · Shift+Enter — новая строка</span>
              {messages.length > 0 && (
                <button type="button" className="link-button" onClick={clearHistory}>
                  {UI_TEXT.clear}
                </button>
              )}
            </div>
          </form>
        </section>

        <aside className="side-panel">
          <section className="side-card">
            <h2>{UI_TEXT.examples}</h2>
            <p className="side-note">
              Формулировки ниже — только примеры. Любой ответ рассчитывается по
              активному набору после отправки вопроса.
            </p>
            <div className="example-list">
              {examples.length === 0 ? (
                <p className="muted">Примеры недоступны.</p>
              ) : (
                examples.map((example) => (
                  <button
                    type="button"
                    className="example"
                    key={`${example.language}-${example.question}`}
                    onClick={() => {
                      setQuestion(example.question);
                      inputRef.current?.focus();
                    }}
                  >
                    <span className="example-top">
                      <span className="example-tag">
                        {example.language === "uz" ? "UZ" : "RU"}
                      </span>
                      <span className="example-badge">{UI_TEXT.exampleBadge}</span>
                    </span>
                    <span>{example.question}</span>
                  </button>
                ))
              )}
            </div>
          </section>

          <section className="side-card about">
            <h2>Как читать ответ</h2>
            <dl>
              <dt>Найдено</dt>
              <dd>Есть проверенные строки и основание расчёта.</dd>
              <dt>Не найдено</dt>
              <dd>Запрос выполнен, совпадений нет; NULL не превращается в 0.</dd>
              <dt>Нужно уточнение</dt>
              <dd>Несколько сущностей или неясный смысл — агент не угадывает.</dd>
              <dt>Не поддержано</dt>
              <dd>Вопрос вне возможностей схемы данных.</dd>
              <dt>Не подтверждено</dt>
              <dd>Техническая ошибка или проваленная проверка.</dd>
            </dl>
          </section>
        </aside>
      </main>

      <footer className="app-footer">
        <span>UZEX Data Agent · только чтение · без регистрации</span>
        <span className="mono">API /api/v1</span>
      </footer>
    </div>
  );
}
