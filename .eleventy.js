const yaml = require("yaml");
const fs = require("fs");

module.exports = function (eleventyConfig) {
  eleventyConfig.addDataExtension("yml", (contents) => {
    return yaml.parse(contents);
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
      /* Generate the author field if it doesn't exist */
      if (!dep.author) {
        /* Use the `id` field, cut the `/` and keep the first part. For the link to the profile,
         * use the repo, cut the last `/<repo>.git` */
        try {
          dep.author = "@" + dep.id.split("/")[0];
          dep.author_link = dep.repo ? dep.repo.replace(/\/[^\/]+\.git$/, "") : null;
        } catch (e) {
          dep.author = "Unknown";
          dep.author_link = "#";
        }
      }

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
};