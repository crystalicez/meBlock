/*******************************************************************************

    uBlock Origin - a browser extension to block requests.
    Copyright (C) 2015-present Raymond Hill

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see {http://www.gnu.org/licenses/}.

    Home: https://github.com/gorhill/uBlock
*/

'use strict';

/******************************************************************************/

// https://github.com/uBlockOrigin/uBlock-issues/issues/756
//   Keep in mind CPU usage with large DOM and/or filterset.

(( ) => {
    if ( typeof vAPI !== 'object' ) { return; }

    const t0 = Date.now();
    const tMax = t0 + 100;

    if ( vAPI.domSurveyElements instanceof Object === false ) {
        vAPI.domSurveyElements = {
            busy: false,
            hiddenElementCount: Number.NaN,
            surveyTime: t0,
        };
    }
    const surveyResults = vAPI.domSurveyElements;

    if ( surveyResults.busy ) { return; }
    surveyResults.busy = true;

    if ( surveyResults.surveyTime < vAPI.domMutationTime ) {
        surveyResults.hiddenElementCount = Number.NaN;
    }
    surveyResults.surveyTime = t0;

    if ( isNaN(surveyResults.hiddenElementCount) ) {
        surveyResults.hiddenElementCount = (( ) => {
            if ( vAPI.domFilterer instanceof Object === false ) { return 0; }
            const details = vAPI.domFilterer.getAllSelectors_(true);
            if (
                Array.isArray(details.declarative) === false ||
                details.declarative.length === 0
            ) {
                return 0;
            }
            const selectors = details.declarative.map(entry => entry[0]);
            const simple = [], complex = [];
            for ( const selectorStr of selectors ) {
                for ( const selector of selectorStr.split(',\n') ) {
                    if ( /[ +>~]/.test(selector) ) {
                        complex.push(selector);
                    } else {
                        simple.push(selector);
                    }
                }
            }
            const simpleStr = simple.join(',\n');
            const complexStr = complex.join(',\n');
            const nodeIter = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_ELEMENT
            );
            const matched = new Set();
            let node = nodeIter.nextNode();
            for (;;) {
                if ( node === null ) { break; }
                if ( Date.now() > tMax ) { return -1; }
                if (
                    (node.offsetParent !== null) ||
                    (simpleStr === '' || node.matches(simpleStr) === false) &&
                    (complexStr === '' || node.closest(complexStr) !== node)
                ) {
                    node = nodeIter.nextNode();
                    continue;
                }
                matched.add(node);
                if ( matched.size === 99 ) { break; }
                // https://github.com/uBlockOrigin/uBlock-issues/issues/756#issuecomment-549079064
                //   Skip descendants when a match is detected.
                for (;;) {
                    node = nodeIter.nextSibling();
                    if ( node !== null ) { break; }
                    node = nodeIter.parentNode();
                    if ( node === null ) { break; }
                }
            }
            return matched.size;
        })();
    }

    surveyResults.busy = false;

    // IMPORTANT: This is returned to the injector, so this MUST be
    //            the last statement.
    return surveyResults.hiddenElementCount;
})();
