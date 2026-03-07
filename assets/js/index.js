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
    var viewButtons = Array.prototype.slice.call(document.querySelectorAll(".view-button"));
    var records = Array.prototype.slice.call(
      document.querySelectorAll("dialog.bbs-modal.bbs-modal__record[data-entry-id]")
    );
    var lastTrigger = null;
    var activeModal = null;
    var restoreFocusOnClose = false;

    if (viewButtons.length === 0 || records.length === 0) {
      return;
    }

    if (typeof records[0].showModal !== "function") {
      return;
    }

    function findRecord(entryId) {
      return (
        records.filter(function (record) {
          return record.getAttribute("data-entry-id") === entryId;
        })[0] || null
      );
    }

    function isModalOpen(modal) {
      return Boolean(modal && modal.open);
    }

    function isVisibleProjectButton(button) {
      var item = button.closest("li[data-name]");
      var section = button.closest("section[data-category]");

      if (!item || !section) {
        return false;
      }

      return !button.hidden && !item.hidden && !section.hidden;
    }

    function getNavigableButtons() {
      return viewButtons.filter(isVisibleProjectButton);
    }

    function openAdjacentEntry(direction) {
      var buttons = getNavigableButtons();
      var activeIndex = buttons.indexOf(lastTrigger);
      var nextIndex;

      if (buttons.length === 0 || activeIndex === -1) {
        return;
      }

      nextIndex = (activeIndex + direction + buttons.length) % buttons.length;

      openModal(buttons[nextIndex]);
    }

    function focusModalTarget() {
      var links = activeModal
        ? Array.prototype.slice.call(activeModal.querySelectorAll("a[href]"))
        : [];
      var closeButton = activeModal ? activeModal.querySelector("[data-close-modal]") : null;
      var dialog = activeModal ? activeModal.querySelector(".bbs-modal__dialog") : null;

      if (links.length > 0) {
        links[0].focus();
        return;
      }

      if (closeButton) {
        closeButton.focus();
        return;
      }

      if (dialog) {
        dialog.focus();
      }
    }

    function closeModal(restoreFocus) {
      if (!activeModal) {
        return;
      }

      restoreFocusOnClose = restoreFocus;
      if (activeModal.open) {
        activeModal.close();
      }
    }

    function openModal(button) {
      var entryId = button.getAttribute("data-entry-id");
      var modal = entryId ? findRecord(entryId) : null;

      if (!modal) {
        return;
      }

      if (activeModal && activeModal !== modal) {
        closeModal(false);
      }

      lastTrigger = button;
      activeModal = modal;
      if (!activeModal.open) {
        activeModal.showModal();
      }
      document.body.classList.add("modal-open");
      focusModalTarget();
    }

    viewButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        openModal(button);
      });
    });

    records.forEach(function (record) {
      record.addEventListener("cancel", function () {
        restoreFocusOnClose = true;
      });

      record.addEventListener("close", function () {
        if (activeModal !== record) {
          return;
        }

        activeModal = null;
        document.body.classList.remove("modal-open");

        if (restoreFocusOnClose && lastTrigger) {
          lastTrigger.focus();
        }

        restoreFocusOnClose = false;
      });

      Array.prototype.slice.call(record.querySelectorAll("[data-close-modal]")).forEach(function (button) {
        button.addEventListener("click", function () {
          closeModal(true);
        });
      });

      record.addEventListener("click", function (event) {
        if (event.target === record) {
          closeModal(true);
        }
      });
    });

    document.addEventListener("keydown", function (event) {
      if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && isModalOpen(activeModal)) {
        if (event.altKey || event.ctrlKey || event.metaKey) {
          return;
        }

        event.preventDefault();
        openAdjacentEntry(event.key === "ArrowRight" ? 1 : -1);
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
