// promptfoo prompt function: the chat messages the harness sends, rendered by PromptAssembler in the test
// generator (harness_prompts.py) and passed in the `messages` var. promptfoo serialises the list as JSON for the
// provider; templating is off (promptfooconfig.yaml), so nothing in them is rendered again.
module.exports.render = ({ vars }) => JSON.parse(vars.messages);
