// Launch status shown on the page. Edit this file to move the bar: each step has a weight (its share of the
// road to launch) and a progress from 0 to 1. The total percentage is worked out from these.
window.MEDNAMA_LAUNCH = {
  updated: "October 2026",
  steps: [
    { title: "The app", detail: "Dr MedNama with cited answers and Tutor me, practice, recalled past papers, re-tests, forecast, offline packs.",
      weight: 30, progress: 1 },
    { title: "Textbook ingestion", detail: "20 standard textbooks read page by page, with figures and page numbers: more than 21,000 pages.",
      weight: 25, progress: 1 },
    { title: "Private beta", detail: "Running now with a small group of students; every report and disputed key is reviewed.",
      weight: 15, progress: 0.6 },
    { title: "Launch pipeline", detail: "Deciding how medNAMA reaches you: an installable app for each student, textbook packs priced per book, and the web version alongside.",
      weight: 20, progress: 0.35 },
    { title: "Public launch", detail: "Open to every FCPS Part 1 candidate.",
      weight: 10, progress: 0 },
  ],
};
