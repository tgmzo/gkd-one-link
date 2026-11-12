import fs from 'node:fs';
import { checkSubscription } from '@gkd-kit/tools';
import {
  Selector,
  SelectorCompileResult,
  SelectorTypeResult,
  createDefaultSelectorTypeModel,
} from '@gkd-kit/selector';

const selectorFields = [
  'matches',
  'anyMatches',
  'excludeMatches',
  'excludeAllMatches',
];
const asArray = (value) =>
  value == null ? [] : Array.isArray(value) ? value : [value];

try {
  const filename = process.argv[2] ?? 'dist/gkd.json5';
  const subscription = JSON.parse(fs.readFileSync(filename, 'utf8'));
  checkSubscription(subscription);

  const typeModel = createDefaultSelectorTypeModel();
  const checked = new Set();
  let groupCount = 0;
  let ruleCount = 0;

  function validateNode(node, location) {
    for (const field of selectorFields) {
      for (const source of asArray(node?.[field])) {
        if (typeof source !== 'string') {
          throw new Error(`${location}.${field}: selector must be a string`);
        }
        if (checked.has(source)) continue;
        const compiled = Selector.compile(source);
        if (!(compiled instanceof SelectorCompileResult.Success)) {
          throw new Error(`${location}.${field}: ${compiled.error.message}`);
        }
        const typed = compiled.value.validateType(typeModel);
        if (!(typed instanceof SelectorTypeResult.Success)) {
          throw new Error(`${location}.${field}: ${typed.error.message}`);
        }
        checked.add(source);
      }
    }
  }

  const families = [
    ['globalGroups', subscription.globalGroups ?? []],
    ...(subscription.apps ?? []).map((app) => [
      `apps[${app.id}].groups`,
      app.groups ?? [],
    ]),
  ];
  for (const [family, groups] of families) {
    for (const group of groups) {
      groupCount++;
      const location = `${family}[${group.key}]`;
      validateNode(group, location);
      for (const [index, rawRule] of asArray(group.rules).entries()) {
        ruleCount++;
        const rule =
          typeof rawRule === 'string' || Array.isArray(rawRule)
            ? { matches: rawRule }
            : rawRule;
        validateNode(rule, `${location}.rules[${rule?.key ?? index}]`);
      }
    }
  }
  console.log(
    `GKD validation passed: ${groupCount} groups, ${ruleCount} rules, ${checked.size} unique selectors.`,
  );
} catch (error) {
  console.error(`GKD validation failed: ${error.message}`);
  if (error.cause?.message) console.error(error.cause.message);
  process.exitCode = 1;
}
