  let uploadPriorOverflow = "";
  const openUploadPage = () => {
    if ($("uploadPage").open) return;
    uploadPriorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    $("uploadPage").showModal();
    $("uploadPage").scrollTop = 0;
  };
  $("uploadDataBtn").addEventListener("click", openUploadPage);
  const openSetupInstructions = () => {
    if (process.open) process.close();
    openUploadPage();
    $("setupInstructions").showModal();
    $("setupInstructions").scrollTop = 0;
    $("copySetupMsg").textContent = "";
  };
  $("firstTimeProcessLink").addEventListener("click", openSetupInstructions);
  $("firstTimeUploadLink").addEventListener("click", openSetupInstructions);
  $("closeSetupInstructions").addEventListener("click", () => $("setupInstructions").close());
  $("setupInstructions").addEventListener("close", () => {
    $("firstTimeUploadLink").focus({ preventScroll: true });
  });
  const copyCodexPrompt = async (promptId, messageId) => {
    try {
      await navigator.clipboard.writeText($(promptId).value);
      $(messageId).textContent = "Copied. Paste the prompt into your Codex chat and send it.";
    } catch (_) {
      $(promptId).focus();
      $(promptId).select();
      $(messageId).textContent = "Select and copy the highlighted prompt, then paste it into your Codex chat.";
    }
  };
  $("copySetupPrompt").addEventListener("click", () => copyCodexPrompt("setupPrompt", "copySetupMsg"));
  $("copyDownloadPrompt").addEventListener("click", () => copyCodexPrompt("downloadPrompt", "copyDownloadMsg"));
  $("closeUploadPage").addEventListener("click", () => $("uploadPage").close());
  $("uploadPage").addEventListener("close", () => {
    document.body.style.overflow = uploadPriorOverflow;
    $("uploadDataBtn").focus({ preventScroll: true });
  });
  $("uploadFile").addEventListener("change", event => previewUpload(event.target.files[0]));
  $("confirmUploadBtn").addEventListener("click", () => uploadData());
