/** Green requires confirmed service, fresh data and a usable workspace.
 * @param {{online?:boolean,dataReachable?:boolean,dataFresh?:boolean|null}|null|undefined} health
 * @param {{ready?:boolean,building?:boolean,error?:string|null}} workspace
 */
export function presentationState(health, workspace) {
  if (
    health?.online === false ||
    (health?.online === true && health.dataReachable === false)
  )
    return "is-unavailable";
  return health?.online === true &&
    health.dataReachable === true &&
    health.dataFresh === true &&
    workspace.ready === true &&
    !workspace.building &&
    !workspace.error
    ? "is-ready"
    : "is-partial";
}
/** @param {HTMLElement} pill @param {string} state */
export function applyPresentationState(pill, state) {
  pill.classList.remove("is-ready", "is-partial", "is-unavailable");
  pill.classList.add(state);
}
