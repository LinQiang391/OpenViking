<template>
  <div>
    <div class="card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
        <h3>Workspace</h3>
        <select id="workspace-select" class="select">
          <option value="default">Default Workspace</option>
        </select>
      </div>
      <div class="file-browser">
        <div class="file-tree">
          <div id="file-tree">Loading...</div>
        </div>
        <div class="file-editor">
          <div id="editor-toolbar" style="margin-bottom: 15px; display: none;">
            <button class="btn btn-primary">Save</button>
            <span id="current-file" style="margin-left: 15px; color: #6b7280;"></span>
          </div>
          <textarea id="file-editor" placeholder="Select a file to edit..." style="display: none;"></textarea>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// Placeholder - to be implemented
</script>
