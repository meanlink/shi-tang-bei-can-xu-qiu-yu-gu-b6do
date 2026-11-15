/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */

/** @typedef {{id:number,code:string,name:string,active:boolean}} Stall */
/** @typedef {{id:number,code:string,name:string,unit:string,active:boolean}} Dish */
/**
 * @typedef {Object} PrepPlan
 * @property {number} id
 * @property {string} data_date
 * @property {number} dish_id
 * @property {number} stall_id
 * @property {number} suggested_qty
 * @property {number|null} approved_job_id
 * @property {'pending'|'approved'} status
 */
/**
 * @typedef {Object} TransferOrder
 * @property {number} id
 * @property {string} order_no
 * @property {number|null} plan_id
 * @property {number} dish_id
 * @property {number} from_stall_id
 * @property {number} to_stall_id
 * @property {number} request_qty
 * @property {number} dispatched_qty
 * @property {number} received_qty
 * @property {number} returned_qty
 * @property {number} on_road_qty
 * @property {'pending'|'in_transit'|'completed_received'|'completed_returned'|'partially_settled'} status
 * @property {string} status_label
 */
/**
 * @typedef {Object} InventoryRow
 * @property {number} stall_id
 * @property {number} dish_id
 * @property {number} prepared_qty
 * @property {number} available_qty
 * @property {number} out_in_transit_qty
 * @property {number} in_in_transit_qty
 * @property {number} consumed_qty
 */
export {};
