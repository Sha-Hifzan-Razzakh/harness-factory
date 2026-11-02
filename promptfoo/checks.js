// promptfoo assertions for the prompt smoke checks (SKP-03). Each reads the prompt promptfoo sent
// (context.prompt, a JSON message list), never the provider's reply, so they mean the same with the offline echo
// provider and with a real model. `vars.expect` is what the prompt must say, worked out by harness_prompts.py
// from the recipe, the task and the tool catalog.

const RESIDUE = /\{\{|\}\}|\{%|%\}|\{#|#\}/g;
const TOOL_TOKEN = /\b[a-z][a-z0-9_]*?__[a-z][a-z0-9_]*\b/g; // a model-facing tool name (server__tool)

const sent = (context) => JSON.parse(context.prompt);
const expect = (context) => JSON.parse(context.vars.expect);
const at = (messages, i) => messages[i < 0 ? messages.length + i : i];
const label = (messages, i) => {
  const idx = i < 0 ? messages.length + i : i;
  return `message ${idx} (${messages[idx].role})`;
};
const result = (problems, ok) => ({
  pass: problems.length === 0,
  score: problems.length === 0 ? 1 : 0,
  reason: problems.length ? problems.join('; ') : ok,
});
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// The comma list on the one line starting with `prefix` ("Available tools: a, b."), or null if there isn't one.
function listed(text, prefix) {
  const lines = text.split('\n').filter((l) => l.startsWith(prefix));
  if (lines.length !== 1) return null;
  return lines[0]
    .slice(prefix.length)
    .replace(/\.$/, '')
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
}

// promptfoo sent exactly the messages the harness renders (its own templating is off).
module.exports.verbatim = (output, context) => {
  const got = sent(context);
  const want = JSON.parse(context.vars.messages);
  const problems = [];
  if (got.length !== want.length) problems.push(`${got.length} messages sent, the harness renders ${want.length}`);
  got.forEach((m, i) => {
    if (i < want.length && !same(m, want[i])) problems.push(`${label(got, i)} differs from the harness render`);
  });
  return result(problems, `${got.length} messages, identical to the harness render`);
};

// No unrendered Jinja ({{ }}, {% %}, {# #}) and no empty message.
module.exports.noResidue = (output, context) => {
  const messages = sent(context);
  const problems = [];
  messages.forEach((m, i) => {
    const text = m.content || '';
    if (!text.trim()) problems.push(`${label(messages, i)} is empty`);
    for (const hit of text.matchAll(RESIDUE)) {
      const around = text.slice(Math.max(0, hit.index - 30), hit.index + 32);
      problems.push(`${label(messages, i)}: template residue '${hit[0]}' in ...${JSON.stringify(around)}...`);
    }
  });
  return result(problems, 'no template residue');
};

// The tool lines name exactly the tools the model is offered at this part, and the planner's tool list matches
// the team's tools (name and description).
module.exports.toolList = (output, context) => {
  const messages = sent(context);
  const exp = expect(context);
  const problems = [];
  for (const line of exp.lines) {
    const text = at(messages, line.message).content;
    const got = listed(text, line.prefix);
    const where = label(messages, line.message);
    if (line.tools.length && got === null) {
      problems.push(`${where}: no single '${line.prefix.trim()}' line (offered: ${line.tools.join(', ')})`);
    } else if (line.tools.length && !same(got, line.tools)) {
      problems.push(`${where}: lists [${got}], offered [${line.tools}]`);
    } else if (!line.tools.length && got && got.length) {
      problems.push(`${where}: lists [${got}] but no tool is offered`);
    } else if (!line.tools.length && line.none && !text.includes(line.none)) {
      problems.push(`${where}: no tool is offered but it doesn't say '${line.none}'`);
    }
  }
  if (exp.planner_tools) {
    const got = [...messages[0].content.matchAll(/^- ([a-z0-9_]+): (.*)$/gm)].map((m) => [m[1], m[2]]);
    const want = exp.planner_tools.map((t) => [t.name, t.description]);
    if (!same(got, want)) {
      problems.push(`planner prompt lists [${got.map((g) => g[0])}], the team has [${want.map((w) => w[0])}]`);
    }
    if (want.some((w) => !w[1])) problems.push('a planner tool has no description');
  }
  const n = exp.lines.reduce((k, l) => k + l.tools.length, 0) + (exp.planner_tools || []).length;
  return result(problems, `tool lines match the offered tools (${n} names checked)`);
};

// Every server__tool name in the harness's instructions is offered there, and a stage that calls itself
// read-only offers only READ tools.
module.exports.toolMentions = (output, context) => {
  const messages = sent(context);
  const exp = expect(context);
  const problems = [];
  for (const scope of exp.mentions) {
    const text = at(messages, scope.message).content;
    const stray = [...new Set(text.match(TOOL_TOKEN) || [])].filter((t) => !scope.allowed.includes(t)).sort();
    if (stray.length) problems.push(`${label(messages, scope.message)} names [${stray}], not offered there`);
  }
  if (exp.read_tools) {
    const stage = at(messages, -1).content;
    if (stage.includes('read-only')) {
      const tools = listed(stage, 'Tools in this stage: ') || [];
      const writes = tools.filter((t) => !exp.read_tools.includes(t));
      if (writes.length) problems.push(`read-only stage offers [${writes}], which are not READ tools`);
    }
  }
  return result(problems, 'names only offered tools');
};

// The markers the shapes parse are in the prompt that asks for them (V3 RETRY:, V4/V5 BLOCKED:, step numbering,
// the last-step note, the plan length cap).
module.exports.markers = (output, context) => {
  const messages = sent(context);
  const exp = expect(context);
  const text = at(messages, exp.marker_message).content;
  const problems = [
    ...exp.markers.filter((m) => !text.includes(m)).map((m) => `missing ${JSON.stringify(m)}`),
    ...exp.unwanted.filter((m) => text.includes(m)).map((m) => `unexpected ${JSON.stringify(m)}`),
  ];
  return result(problems, exp.markers.length ? `markers present (${exp.markers.length})` : 'no markers at this part');
};

// The task instruction reaches the model unchanged (for a V5 worker: inside its part, as the overall task).
module.exports.task = (output, context) => {
  const messages = sent(context);
  const exp = expect(context);
  const first = messages.find((m) => m.role === 'user');
  const ok = first && first.content.includes(exp.instruction);
  return result(ok ? [] : ['the first user message does not contain the task instruction'], 'task instruction present');
};

// Character budgets: each message and the whole prompt.
module.exports.length = (output, context) => {
  const messages = sent(context);
  const exp = expect(context);
  const problems = [];
  messages.forEach((m, i) => {
    if (m.content.length > exp.caps[i]) problems.push(`${label(messages, i)} is ${m.content.length} chars, budget ${exp.caps[i]}`);
  });
  const total = messages.reduce((k, m) => k + m.content.length, 0);
  if (total > exp.total_chars) problems.push(`prompt is ${total} chars, budget ${exp.total_chars}`);
  return result(problems, `${total} chars, within budget`);
};
