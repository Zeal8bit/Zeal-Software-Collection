(function () {
  function initCollectionFilters() {
    var categoryInput = document.getElementById("collection-category");
    var searchInput = document.getElementById("collection-search");
    var statusNode = document.getElementById("collection-status");
    var sections = Array.prototype.slice.call(document.querySelectorAll("main section[data-category]"));

    if (!categoryInput || !searchInput || !statusNode || sections.length === 0) {
      return;
    }

    function pluralize(count) {
      return count === 1 ? "ENTRY" : "ENTRIES";
    }

    function updateStatus(visibleCount, selectedCategory, searchTerm) {
      if (visibleCount === 0) {
        statusNode.textContent = "NO MATCHES FOUND.";
        return;
      }

      var parts = ["SHOWING " + visibleCount + " " + pluralize(visibleCount)];

      if (selectedCategory) {
        parts.push("CATEGORY: " + selectedCategory.toUpperCase());
      }

      if (searchTerm) {
        parts.push('SEARCH: "' + searchTerm.toUpperCase() + '"');
      }

      statusNode.textContent = parts.join(" | ") + ".";
    }

    function applyFilters() {
      var selectedCategory = categoryInput.value;
      var searchTerm = searchInput.value.trim().toLowerCase();
      var visibleCount = 0;

      sections.forEach(function (section) {
        var sectionCategory = section.getAttribute("data-category");
        var categoryMatch = !selectedCategory || sectionCategory === selectedCategory;
        var items = Array.prototype.slice.call(section.querySelectorAll("li[data-name]"));
        var visibleInSection = 0;

        items.forEach(function (item) {
          var itemName = item.getAttribute("data-name") || "";
          var searchMatch = !searchTerm || itemName.indexOf(searchTerm) !== -1;
          var isVisible = categoryMatch && searchMatch;

          item.hidden = !isVisible;

          if (isVisible) {
            visibleInSection += 1;
            visibleCount += 1;
          }
        });

        section.hidden = visibleInSection === 0;
      });

      updateStatus(visibleCount, selectedCategory, searchTerm);
    }

    categoryInput.addEventListener("change", applyFilters);
    searchInput.addEventListener("input", applyFilters);
    applyFilters();
  }

  function initEntryModal() {
    var modal = document.getElementById("entry-modal");
    var content = document.getElementById("entry-modal-content");
    var title = document.getElementById("entry-modal-title");
    var dialog = modal ? modal.querySelector(".bbs-modal__dialog") : null;
    var viewButtons = Array.prototype.slice.call(document.querySelectorAll(".view-button"));
    var closeButtons = Array.prototype.slice.call(document.querySelectorAll("[data-close-modal]"));
    var lastTrigger = null;

    if (!modal || !content || !title || !dialog || viewButtons.length === 0) {
      return;
    }

    function escapeHtml(text) {
      return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function normalizeYamlIndentation(text) {
      var lines = text.split("\n");
      var activeListIndent = null;
      var activeParentIndent = null;

      lines.forEach(function (line, index) {
        var keyMatch = line.match(/^(\s*)([^:\n]+):\s*$/);
        var lineIndent = (line.match(/^\s*/) || [""])[0].length;

        if (keyMatch) {
          activeParentIndent = keyMatch[1].length;
          activeListIndent = null;
          return;
        }

        if (/^\s*$/.test(line)) {
          return;
        }

        if (/^-\s+/.test(line) && activeParentIndent !== null) {
          activeListIndent = activeParentIndent + 2;
          lines[index] = new Array(activeListIndent + 1).join(" ") + line;
          return;
        }

        if (/^-\s+/.test(line) && activeListIndent !== null) {
          lines[index] = new Array(activeListIndent + 1).join(" ") + line;
          return;
        }

        if (lineIndent <= (activeParentIndent === null ? 0 : activeParentIndent)) {
          activeParentIndent = null;
          activeListIndent = null;
        }
      });

      return lines.join("\n");
    }

    function linkifyYaml(text) {
      var escaped = escapeHtml(text);

      return escaped.replace(/https?:\/\/[^\s]+/g, function (url) {
        return '<a href="' + url + '" target="_blank" rel="noreferrer noopener">' + url + "</a>";
      });
    }

    function getFocusableNodes() {
      return Array.prototype.slice
        .call(
          dialog.querySelectorAll(
            'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
          )
        )
        .filter(function (node) {
          return !node.hidden;
        });
    }

    function focusModalTarget() {
      var yamlLinks = Array.prototype.slice.call(content.querySelectorAll("a[href]"));

      if (yamlLinks.length > 0) {
        yamlLinks[0].focus();
        return;
      }

      if (closeButtons.length > 0) {
        closeButtons[0].focus();
        return;
      }

      dialog.focus();
    }

    function closeModal() {
      modal.hidden = true;
      document.body.classList.remove("modal-open");

      if (lastTrigger) {
        lastTrigger.focus();
      }
    }

    function openModal(button) {
      var entry = button.closest("li");
      var yamlNode = entry ? entry.querySelector(".entry-yaml") : null;
      var entryName = button.getAttribute("data-entry-name") || "PROJECT RECORD";

      if (!yamlNode) {
        return;
      }

      lastTrigger = button;
      title.textContent = entryName.toUpperCase() + " :: PROJECT RECORD";
      content.innerHTML = linkifyYaml(normalizeYamlIndentation(yamlNode.textContent.trim()));
      modal.hidden = false;
      document.body.classList.add("modal-open");
      focusModalTarget();
    }

    viewButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        openModal(button);
      });
    });

    closeButtons.forEach(function (button) {
      button.addEventListener("click", closeModal);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Tab" && !modal.hidden) {
        var focusable = getFocusableNodes();

        if (focusable.length === 0) {
          event.preventDefault();
          dialog.focus();
          return;
        }

        var first = focusable[0];
        var last = focusable[focusable.length - 1];

        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
          return;
        }

        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
          return;
        }
      }

      if (event.key === "Escape" && !modal.hidden) {
        closeModal();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initCollectionFilters();
      initEntryModal();
    });
  } else {
    initCollectionFilters();
    initEntryModal();
  }
})();
