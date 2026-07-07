# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/kodsama/Skrooge2Firefly/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                         |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| src/skrooge2firefly/\_\_init\_\_.py          |        1 |        0 |        0 |        0 |    100% |           |
| src/skrooge2firefly/\_\_main\_\_.py          |        3 |        3 |        2 |        0 |      0% |       3-6 |
| src/skrooge2firefly/app.py                   |      106 |       11 |       50 |       12 |     85% |148, 150, 152, 154, 164, 166, 168, 170, 191, 192-\>194, 203, 237 |
| src/skrooge2firefly/cli.py                   |      149 |       20 |       38 |        8 |     83% |193, 214-216, 230-232, 253-256, 295-307, 330, 332-340, 350, 372, 383 |
| src/skrooge2firefly/config.py                |       22 |        0 |        2 |        0 |    100% |           |
| src/skrooge2firefly/export/\_\_init\_\_.py   |        0 |        0 |        0 |        0 |    100% |           |
| src/skrooge2firefly/export/puller.py         |       39 |        0 |        6 |        0 |    100% |           |
| src/skrooge2firefly/export/qif.py            |       93 |        1 |       44 |        4 |     96% |35, 107-\>69, 108-\>113, 113-\>69 |
| src/skrooge2firefly/export/skg.py            |      172 |        5 |       66 |        8 |     95% |67-70, 87-\>90, 116-\>122, 158, 269-270, 284-\>248, 286-\>301, 301-\>248 |
| src/skrooge2firefly/export/verify.py         |      100 |        8 |       44 |        5 |     90% |63, 79-82, 85, 99, 106 |
| src/skrooge2firefly/export\_cli.py           |       80 |       12 |       16 |        5 |     82% |92-93, 101-102, 140, 146, 150-151, 160-162, 166 |
| src/skrooge2firefly/model/\_\_init\_\_.py    |        0 |        0 |        0 |        0 |    100% |           |
| src/skrooge2firefly/model/currency.py        |       26 |        0 |       14 |        0 |    100% |           |
| src/skrooge2firefly/model/entities.py        |       67 |        0 |        0 |        0 |    100% |           |
| src/skrooge2firefly/model/mapper.py          |      309 |       15 |      120 |        9 |     94% |296-297, 452-455, 498-504, 552-556, 558-561, 623, 626, 633, 675-\>677 |
| src/skrooge2firefly/skrooge/\_\_init\_\_.py  |        0 |        0 |        0 |        0 |    100% |           |
| src/skrooge2firefly/skrooge/reader.py        |      200 |        5 |       42 |        1 |     98% |34-35, 183, 436-437 |
| src/skrooge2firefly/verify.py                |      137 |       11 |       42 |        3 |     89% |53-54, 195-204, 239-\>236 |
| src/skrooge2firefly/writers/\_\_init\_\_.py  |        0 |        0 |        0 |        0 |    100% |           |
| src/skrooge2firefly/writers/base.py          |       27 |        0 |        2 |        0 |    100% |           |
| src/skrooge2firefly/writers/client.py        |      219 |       24 |       74 |        9 |     87% |123-128, 133, 139, 157, 209-\>229, 214-221, 233-234, 252, 260, 262-\>261, 263-\>262, 265-266, 377 |
| src/skrooge2firefly/writers/csv\_importer.py |       44 |        3 |       14 |        2 |     88% |70-\>73, 81-86 |
| src/skrooge2firefly/writers/firefly\_api.py  |      399 |       34 |      168 |       13 |     91% |91-92, 99, 116-\>114, 124-\>exit, 242, 301-304, 306, 334-337, 341, 414-416, 421, 449, 454-455, 461-465, 471, 526, 542-543, 602-605 |
| **TOTAL**                                    | **2193** |  **152** |  **744** |   **79** | **91%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/kodsama/Skrooge2Firefly/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/kodsama/Skrooge2Firefly/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/kodsama/Skrooge2Firefly/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/kodsama/Skrooge2Firefly/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fkodsama%2FSkrooge2Firefly%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/kodsama/Skrooge2Firefly/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.