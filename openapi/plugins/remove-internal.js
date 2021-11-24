const id = 'ld';

/** @type {import('@redocly/openapi-cli').CustomRulesConfig} */
const decorators = {
  oas3: {
    'remove-internal-operations': () => {
      return {
        PathItem: {
          leave(pathItem, ctx) {
            // delete if the path itself is marked with x-internal
            if (pathItem['x-internal']) {
              delete ctx.parent[ctx.key];
            }

            // delete any operations inside of a path marked with x-internal
            const operations = ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'];
            for (const operation of operations) {
              if (pathItem[operation] && pathItem[operation]['x-internal']) {
                delete pathItem[operation];
              }
            }

            // delete the path if there are no operations remaining in it
            if (Object.keys(pathItem).length === 0) {
              delete ctx.parent[ctx.key];
            }
          }
        }
      }
    },
  },
};

module.exports = {
  id,
  decorators,
};
