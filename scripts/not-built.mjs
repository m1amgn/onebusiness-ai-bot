const [command, stage] = process.argv.slice(2);
console.error(
  command && stage
    ? `yarn ${command} is not available yet: it is built in stage ${stage}.`
    : 'Usage: not-built.mjs <command> <stage>',
);
process.exitCode = 1;
