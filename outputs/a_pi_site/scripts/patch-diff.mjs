export function patchChanges(oldText, newText) {
  const left=oldText.trimEnd().split(/\r?\n/), right=newText.trimEnd().split(/\r?\n/);
  const matrix=Array.from({length:left.length+1},()=>new Uint32Array(right.length+1));
  for(let i=left.length-1;i>=0;i--)for(let j=right.length-1;j>=0;j--)matrix[i][j]=left[i]===right[j]?matrix[i+1][j+1]+1:Math.max(matrix[i+1][j],matrix[i][j+1]);
  const operations=[];let i=0,j=0;
  while(i<left.length||j<right.length){
    if(i<left.length&&j<right.length&&left[i]===right[j]){operations.push(' '+left[i++]);j++;}
    else if(j<right.length&&(i===left.length||matrix[i][j+1]>=matrix[i+1][j]))operations.push('+'+right[j++]);
    else operations.push('-'+left[i++]);
  }
  const intervals=[];
  for(let index=0;index<operations.length;index++)if(operations[index][0]!==' '){
    const start=Math.max(0,index-2),end=Math.min(operations.length,index+3),previous=intervals.at(-1);
    if(previous&&start<=previous[1])previous[1]=end;else intervals.push([start,end]);
  }
  return intervals.map(([start,end])=>'@@\n'+operations.slice(start,end).join('\n')+'\n').join('');
}
