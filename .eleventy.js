const yaml = require("yaml");
const fs = require("fs");
const ejsPlugin = require("@11ty/eleventy-plugin-ejs");

module.exports = function (eleventyConfig) {
  eleventyConfig.addDataExtension("yml", (contents) => {
    return yaml.parse(contents);
  });

  eleventyConfig.addPlugin(ejsPlugin);

  eleventyConfig.addFilter("jsonify", (data) => {
    return JSON.stringify(data, null, 2);
  });

  eleventyConfig.addFilter('includes', function(arr, value) {
    return arr && arr.indexOf(value) !== -1;
  });

  eleventyConfig.addFilter("pre_emoji", (text) => {
    const emojis = {"game" : "🕹️",
                   "demo"  : "💾",
                   "software" : "💻",
                   "library" : "📚",
                   "development-tool" : "🛠️",
                   "language": "🔤", 
                   "service" : "🔧",
                   "core" : "⚡",
                   "extra" : "✨",
                   "hardware" : "⚙️",
                  };
    let emoji = emojis[text.toLowerCase()] || "❓";
    return `${emoji} ${text}`;
  });

  /* The collection.yml file is not in the _data directory, and must be lept at the root */
  eleventyConfig.addGlobalData("collection", () => {
    const contents = fs.readFileSync("./collection.yml", "utf8");
    const data = yaml.parse(contents);

    /* Group the entries by categories */
    const grouped = {};

    for (const dep of data.dependencies || []) {
      const categories = dep.metadata?.category || [];

      /* Browse all the categories of the current entry */
      for (const cat of categories) {
        /* Create the category if it doesn't exist */
        if (!grouped[cat]) {
          grouped[cat] = [];
        }
        grouped[cat].push(dep);
      }
    }

    const ret = { categories: grouped, raw: data };
    return ret;
  });

  return {
    dir: {
      input: "src",
      output: "_site",
      includes: "_includes",
      data: "_data"
    }
  };
};